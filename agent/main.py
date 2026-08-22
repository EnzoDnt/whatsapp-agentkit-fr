"""Serveur FastAPI : webhook WhatsApp + simulateur local."""

from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse

from agent.brain import MODE_TRANSPARENCE, generer_reponse, message_erreur
from agent.memory import (
    basculer_pause_conversation,
    conversation_en_pause,
    enregistrer_escalade,
    effacer_historique,
    enregistrer_message,
    initialiser_base,
    toucher_contact,
    liberer_evenement,
    marquer_evenement_traite,
    nettoyer_evenements_anciens,
    obtenir_historique,
    purger_donnees_expirees,
)
from agent.providers import ErreurConfiguration, MessageEntrant, obtenir_fournisseur
from agent.securite import (
    autorise_a_repondre,
    depenses,
    limiteur,
    masquer_contenu,
    masquer_identifiant,
)

load_dotenv()

from agent.environnement import (  # noqa: E402
    audit_configuration,
    est_developpement_declare,
    est_production,
)

ENVIRONNEMENT = "production" if est_production() else "development"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
# En développement on veut le détail de NOTRE agent, pas celui des librairies :
# passer la racine en DEBUG noie la console sous les logs de httpx et aiosqlite.
for bruyant in ("httpx", "httpcore", "aiosqlite", "sqlalchemy.engine"):
    logging.getLogger(bruyant).setLevel(logging.WARNING)
logger = logging.getLogger("agentkit")

# Un verrou par numéro : sur WhatsApp il est courant d'envoyer « bonjour » puis
# la vraie question dans la foulée. Sans ça, les deux messages seraient traités
# en parallèle, liraient le même historique et les écritures s'entremêleraient.
#
# Le dictionnaire est purgé : un verrou par correspondant, gardé à vie, c'est
# une fuite de mémoire lente mais certaine — quelques dizaines d'octets par
# client, indéfiniment. Sur un agent qui tourne un an, cela finit par compter.
_verrous: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
LIMITE_VERROUS = 5000


def _purger_verrous() -> None:
    """Oublie les verrous libres quand le dictionnaire devient gros."""
    if len(_verrous) <= LIMITE_VERROUS:
        return
    for cle in [c for c, v in list(_verrous.items()) if not v.locked()]:
        del _verrous[cle]

fournisseur = None
erreur_configuration: str | None = None
etat_fournisseur = {"ok": False, "detail": "non vérifié"}
alertes_configuration: list[dict] = []


@asynccontextmanager
async def cycle_de_vie(app: FastAPI):
    global fournisseur, erreur_configuration, etat_fournisseur, alertes_configuration

    # Revue de configuration avant tout le reste. Une console ouverte à tous ou
    # un historique qui s'efface à chaque déploiement ne lèvent aucune exception :
    # sans ce contrôle, rien ne les signale avant que le mal soit fait.
    alertes_configuration = audit_configuration()
    for a in alertes_configuration:
        ligne = f"[{a['gravite'].upper()}] {a['sujet']} — {a['explication']} → {a['remede']}"
        (logger.error if a["gravite"] in ("critique", "haute") else logger.warning)(ligne)
    if not alertes_configuration:
        logger.info("Revue de configuration : rien à signaler")

    await initialiser_base()
    await nettoyer_evenements_anciens()
    await purger_donnees_expirees()

    try:
        fournisseur = obtenir_fournisseur()
    except ErreurConfiguration as e:
        # On démarre quand même : le health check expliquera le problème.
        # Mourir à l'import laisserait l'hébergeur redémarrer en boucle sans message.
        erreur_configuration = str(e)
        logger.error(f"Configuration invalide :\n{e}")
    else:
        app.state.fournisseur = fournisseur
        logger.info(f"Fournisseur WhatsApp : {fournisseur.nom}")
        ok, detail = await fournisseur.verifier_connexion()
        etat_fournisseur = {"ok": ok, "detail": detail}
        logger.info(f"Connexion fournisseur : {'OK' if ok else 'ERREUR'} — {detail}")

    yield


app = FastAPI(title="AgentKit FR — Agent WhatsApp", version="1.0.0", lifespan=cycle_de_vie)

# Back-office : monté seulement si ADMIN_TOKEN est défini. Il expose des
# conversations clients, il ne doit jamais être accessible par défaut.
from agent.admin import ADMIN_TOKEN, routeur as routeur_admin  # noqa: E402

if ADMIN_TOKEN:
    app.include_router(routeur_admin)
    logger.info("Back-office actif sur /admin (protégé par ADMIN_TOKEN)")
else:
    logger.info("Back-office désactivé : ADMIN_TOKEN non défini")


@app.get("/")
async def sante():
    """Point de santé pour l'hébergeur et le diagnostic."""
    if erreur_configuration:
        return {"statut": "erreur", "detail": erreur_configuration}

    graves = [a for a in alertes_configuration if a["gravite"] in ("critique", "haute")]
    if etat_fournisseur["ok"] and not graves:
        statut = "ok"
    elif graves:
        statut = "a_corriger"
    else:
        statut = "degrade"

    return {
        "statut": statut,
        "fournisseur": fournisseur.nom if fournisseur else None,
        "connexion": etat_fournisseur,
        "environnement": ENVIRONNEMENT,
        "depense_du_jour_usd": depenses.depense_du_jour,
        # Exposé à dessein : ce sont des noms de variables à renseigner, jamais
        # leur valeur. C'est le seul endroit où la personne qui a déployé ira
        # vraiment regarder.
        "a_corriger": alertes_configuration,
    }


@app.get("/webhook")
async def verification_webhook(request: Request):
    """Vérification GET demandée par Meta lors de l'abonnement."""
    if fournisseur is None:
        raise HTTPException(503, erreur_configuration or "Fournisseur non configuré")

    reponse = await fournisseur.valider_webhook(request)
    if reponse is not None:
        logger.info("Webhook vérifié par Meta avec succès")
        return PlainTextResponse(reponse)

    # Meta attend un 403 si le verify_token ne correspond pas. Répondre 200
    # lui ferait croire que l'URL est validée alors qu'elle ne l'est pas.
    if request.query_params.get("hub.mode") == "subscribe":
        logger.warning("Vérification de webhook refusée : verify_token incorrect")
        raise HTTPException(403, "Verify token incorrect")

    return {"statut": "ok"}


@app.post("/webhook")
async def reception_webhook(request: Request, taches: BackgroundTasks):
    """
    Réception des messages WhatsApp.

    Répond 200 immédiatement et traite en arrière-plan : les fournisseurs
    attendent un 2xx sous ~5 secondes et réessaient jusqu'à 7 fois sinon.
    Un appel à Claude dépasse ce délai — sans ce découplage, le client
    recevrait sept fois la même réponse.
    """
    if fournisseur is None:
        raise HTTPException(503, erreur_configuration or "Fournisseur non configuré")

    if not await fournisseur.verifier_signature(request):
        raise HTTPException(401, "Signature de webhook invalide")

    try:
        messages = await fournisseur.parser_webhook(request)
    except Exception as e:  # noqa: BLE001
        # Un payload inattendu ne doit pas provoquer de réessais infinis.
        logger.error(f"Webhook illisible : {e}")
        return {"statut": "ignore"}

    empiles = 0
    for msg in messages:
        if msg.est_sortant or not msg.texte.strip():
            continue

        # Garde-fou de test : sur un numéro déjà en production, on ne veut pas
        # qu'un vrai client tombe sur un agent en cours de configuration.
        if not autorise_a_repondre(msg.identifiant):
            logger.info(
                f"Hors liste blanche de test : "
                f"{masquer_identifiant(msg.identifiant, msg.par_bsuid)} ignoré (aucune réponse envoyée)"
            )
            continue

        # Déduplication AVANT la limite de débit, et non l'inverse.
        #
        # Meta rejoue un événement jusqu'à sept fois tant qu'il n'a pas reçu son
        # 2xx. Compter le débit d'abord faisait payer ces réessais au client :
        # un seul message rejoué cinq fois consommait cinq jetons sur les vingt
        # de sa fenêtre horaire, et il se retrouvait muselé sans avoir rien fait.
        evenement_id = msg.contexte.get("evenement_id") or msg.message_id
        if evenement_id and not await marquer_evenement_traite(evenement_id):
            logger.info("Événement déjà traité, ignoré")
            continue

        autorise, restants = limiteur.autoriser(msg.identifiant)
        if not autorise:
            logger.warning(
                f"Limite de débit atteinte pour {masquer_identifiant(msg.identifiant, msg.par_bsuid)} : message ignoré"
            )
            # L'événement a été marqué traité juste au-dessus : on le libère,
            # sinon un réessai légitime serait écarté comme un doublon.
            await liberer_evenement(evenement_id)
            continue

        logger.info(
            f"Message de {masquer_identifiant(msg.identifiant, msg.par_bsuid)}"
            f"{' (via username)' if msg.par_bsuid else ''} : {masquer_contenu(msg.texte)} "
            f"({restants} restants dans la fenêtre)"
        )
        _purger_verrous()
        taches.add_task(traiter_message, msg)
        empiles += 1

    return {"statut": "ok", "empiles": empiles}


async def traiter_message(msg: MessageEntrant) -> None:
    """Génère la réponse et la renvoie. S'exécute hors du cycle du webhook."""
    evenement_id = msg.contexte.get("evenement_id") or msg.message_id

    async with _verrous[msg.identifiant]:
        try:
            # Un humain a repris la main : on enregistre le message du client
            # pour qu'il apparaisse dans le back-office, mais l'agent se tait.
            if await conversation_en_pause(msg.identifiant):
                await enregistrer_message(msg.identifiant, "user", msg.texte)
                logger.info(
                    f"Conversation en pause ({masquer_identifiant(msg.identifiant, msg.par_bsuid)}) : "
                    "l'agent ne répond pas"
                )
                return

            # L'historique est lu AVANT d'enregistrer le message courant :
            # brain.py ajoute le nouveau message à la fin, sinon il serait en double.
            await toucher_contact(
                msg.identifiant,
                nom_whatsapp=msg.contexte.get("nom_profil", ""),
                username=msg.username,
                pays=msg.contexte.get("pays", ""),
            )
            historique = await obtenir_historique(msg.identifiant)
            reponse, vraie_reponse = await generer_reponse(
                msg.texte, historique, telephone=msg.identifiant
            )

            # Mode « validation humaine » : la réponse devient un brouillon
            # soumis à l'équipe au lieu de partir directement. C'est le seul
            # mode où l'AI Act n'exige aucun marquage, puisqu'une personne
            # endosse la responsabilité éditoriale de chaque message.
            if MODE_TRANSPARENCE == "validation" and vraie_reponse:
                await enregistrer_message(msg.identifiant, "user", msg.texte)
                await enregistrer_escalade(
                    identifiant=msg.identifiant,
                    motif="Validation humaine requise avant envoi",
                    question_equipe="",
                    reponse_proposee=reponse,
                )
                await basculer_pause_conversation(msg.identifiant, True)
                logger.info("Réponse mise en attente de validation humaine")
                return

            envoye = await fournisseur.envoyer_message(msg.identifiant, reponse, msg.contexte)

            if not envoye:
                # L'événement a été marqué traité en amont pour bloquer les doublons.
                # Si l'envoi échoue il faut le libérer : sinon le réessai du
                # fournisseur serait écarté et le client n'aurait jamais de réponse.
                logger.error("Envoi impossible : l'événement est libéré pour réessai")
                await liberer_evenement(evenement_id)
                return

            # Le message du CLIENT est toujours conservé, même quand l'agent a
            # échoué : sinon la trace disparaît alors que c'est justement le cas
            # où un humain doit reprendre la main depuis le back-office.
            await enregistrer_message(msg.identifiant, "user", msg.texte)

            # La réponse de l'AGENT n'y entre que si c'en est vraiment une. Un
            # avis technique (« problème technique ») n'est pas un tour de
            # dialogue : le garder polluerait le contexte des messages suivants.
            if vraie_reponse:
                await enregistrer_message(msg.identifiant, "assistant", reponse)
            else:
                logger.warning(
                    "Le client n'a pas eu de vraie réponse : à reprendre depuis /admin"
                )

            logger.info(f"Réponse envoyée à {masquer_identifiant(msg.identifiant, msg.par_bsuid)}")

        except Exception as e:  # noqa: BLE001
            logger.exception(f"Erreur de traitement : {e}")
            await liberer_evenement(evenement_id)
            try:
                await fournisseur.envoyer_message(msg.identifiant, message_erreur(), msg.contexte)
            except Exception:  # noqa: BLE001
                logger.error("Impossible même de prévenir le client")


# ── Simulateur local ─────────────────────────────────────────────────────
#
# Canal SANS authentification : qui connaît l'URL peut injecter des messages et
# consommer le crédit du modèle. Il se monte donc sur demande expresse
# (ENVIRONMENT=development) et non « partout sauf en production », règle qui
# l'ouvrait au moindre oubli de configuration.

if est_developpement_declare():
    from agent.providers.simulateur import file_sortante, payload_signe

    RACINE = Path(__file__).resolve().parent.parent

    @app.get("/simulateur")
    async def page_simulateur():
        return FileResponse(RACINE / "simulateur" / "index.html")

    @app.post("/simulateur/envoyer")
    async def simulateur_envoyer(corps: dict):
        """
        Fabrique un vrai webhook Meta signé et le poste sur /webhook.

        On ne court-circuite pas le webhook : le message traverse la
        vérification de signature, la déduplication et la file de tâches,
        exactement comme en production.
        """
        texte = (corps.get("texte") or "").strip()
        if not texte:
            raise HTTPException(400, "Message vide")

        charge, entetes = payload_signe(texte)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://simulateur") as c:
            r = await c.post("/webhook", content=charge, headers=entetes)
        return {"statut": r.status_code, "reponse_webhook": r.json()}

    @app.get("/simulateur/messages")
    async def simulateur_messages(depuis: int = 0):
        messages, index = await file_sortante.depuis(depuis)
        return {"messages": messages, "index": index}

    @app.post("/simulateur/reinitialiser")
    async def simulateur_reinitialiser():
        from agent.providers.simulateur import TELEPHONE_SIMULE

        await file_sortante.vider()
        n = await effacer_historique(TELEPHONE_SIMULE)
        return {"statut": "ok", "messages_effaces": n}
