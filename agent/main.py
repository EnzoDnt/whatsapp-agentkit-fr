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

from agent.brain import generer_reponse, message_erreur
from agent.memory import (
    effacer_historique,
    enregistrer_message,
    initialiser_base,
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

ENVIRONNEMENT = os.getenv("ENVIRONMENT", "development").strip().lower()
EST_PRODUCTION = ENVIRONNEMENT == "production"

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
_verrous: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

fournisseur = None
erreur_configuration: str | None = None
etat_fournisseur = {"ok": False, "detail": "non vérifié"}


@asynccontextmanager
async def cycle_de_vie(app: FastAPI):
    global fournisseur, erreur_configuration, etat_fournisseur

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
from agent.admin import ADMIN_TOKEN, est_en_pause, routeur as routeur_admin  # noqa: E402

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
    return {
        "statut": "ok" if etat_fournisseur["ok"] else "degrade",
        "fournisseur": fournisseur.nom if fournisseur else None,
        "connexion": etat_fournisseur,
        "environnement": ENVIRONNEMENT,
        "depense_du_jour_usd": depenses.depense_du_jour,
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

        autorise, restants = limiteur.autoriser(msg.identifiant)
        if not autorise:
            logger.warning(
                f"Limite de débit atteinte pour {masquer_identifiant(msg.identifiant, msg.par_bsuid)} : message ignoré"
            )
            continue

        # Livraison « au moins une fois » : le même événement peut arriver deux fois.
        evenement_id = msg.contexte.get("evenement_id") or msg.message_id
        if evenement_id and not await marquer_evenement_traite(evenement_id):
            logger.info("Événement déjà traité, ignoré")
            continue

        logger.info(
            f"Message de {masquer_identifiant(msg.identifiant, msg.par_bsuid)}"
            f"{' (via username)' if msg.par_bsuid else ''} : {masquer_contenu(msg.texte)} "
            f"({restants} restants dans la fenêtre)"
        )
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
            if ADMIN_TOKEN and await est_en_pause(msg.identifiant):
                await enregistrer_message(msg.identifiant, "user", msg.texte)
                logger.info(
                    f"Conversation en pause ({masquer_identifiant(msg.identifiant, msg.par_bsuid)}) : "
                    "l'agent ne répond pas"
                )
                return

            # L'historique est lu AVANT d'enregistrer le message courant :
            # brain.py ajoute le nouveau message à la fin, sinon il serait en double.
            historique = await obtenir_historique(msg.identifiant)
            reponse, vraie_reponse = await generer_reponse(
                msg.texte, historique, telephone=msg.identifiant
            )

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
# Monté uniquement hors production : c'est un canal non authentifié.

if not EST_PRODUCTION:
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
