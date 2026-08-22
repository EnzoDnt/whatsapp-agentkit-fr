"""
Back-office minimal : voir les conversations et reprendre la main.

Volontairement réduit à l'essentiel. Ce n'est pas un CRM : c'est la fenêtre
qui permet de lire ce que l'agent a répondu, de le mettre en pause sur une
conversation, et de répondre soi-même.

Protégé par ADMIN_TOKEN. Sans ce jeton, les routes ne sont pas montées du tout :
cette interface expose des conversations clients, elle ne doit jamais être
ouverte par défaut.
"""

from __future__ import annotations

import hmac
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import DateTime, String, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from agent.auth import (
    Utilisateur,
    aucun_utilisateur,
    authentifier,
    changer_mot_de_passe,
    creer_jeton,
    creer_utilisateur,
    definir_activation,
    exiger_secret,
    limiteur_connexion,
    poser_cookie,
    retirer_cookie,
    utilisateur_courant,
)
from agent.memory import Message, Session, enregistrer_message
from agent.securite import masquer_identifiant

logger = logging.getLogger("agentkit")

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()


from agent.memory import (  # noqa: E402
    basculer_pause_conversation as basculer_pause,
    conversation_en_pause as est_en_pause,
)


async def verifier_jeton(request: Request) -> None:
    """
    Garde d'accès de toutes les routes du back-office.

    L'accès repose sur une session signée déposée à la connexion. ADMIN_TOKEN ne
    sert plus qu'à deux choses : décider si la console est montée du tout, et
    autoriser la création du tout premier compte (voir /admin/amorcer).
    """
    await utilisateur_courant(request)


routeur = APIRouter(prefix="/admin", tags=["admin"])


# ═════════════════════════════════════════════════════════════════════════
# Authentification
# ═════════════════════════════════════════════════════════════════════════


@routeur.get("/session")
async def etat_session(request: Request):
    """
    Sondé par la page avant d'afficher quoi que ce soit.

    Trois réponses possibles : aucun compte n'existe (il faut amorcer),
    personne n'est connecté, ou voici qui vous êtes.
    """
    if await aucun_utilisateur():
        return {"etat": "amorcage_requis"}
    try:
        u = await utilisateur_courant(request)
    except HTTPException:
        return {"etat": "deconnecte"}
    return {"etat": "connecte", "nom": u.nom, "email": u.email}


@routeur.post("/amorcer")
async def amorcer(corps: dict, request: Request, reponse: Response):
    """
    Crée le tout premier compte.

    Protégé par ADMIN_TOKEN, connu seulement de qui a accès au fichier .env.
    Sans cette barrière, le premier visiteur d'une console fraîchement déployée
    s'attribuerait le compte administrateur.
    """
    exiger_secret()
    if not await aucun_utilisateur():
        raise HTTPException(409, "Un compte existe déjà. Connectez-vous.")
    fourni = (corps.get("jeton") or "").strip()
    if not ADMIN_TOKEN or not hmac.compare_digest(fourni, ADMIN_TOKEN):
        raise HTTPException(401, "Jeton d'installation incorrect")

    identifiant = await creer_utilisateur(
        email=corps.get("email", ""),
        nom=corps.get("nom", ""),
        mot_de_passe=corps.get("mot_de_passe", ""),
    )
    poser_cookie(reponse, creer_jeton(identifiant), request)
    return {"etat": "connecte"}


@routeur.post("/connexion")
async def connexion(corps: dict, request: Request, reponse: Response):
    exiger_secret()
    ip = request.client.host if request.client else "inconnue"
    if not limiteur_connexion.autorise(ip):
        raise HTTPException(429, "Trop de tentatives. Réessayez dans quelques minutes.")

    u = await authentifier(corps.get("email", ""), corps.get("mot_de_passe", ""))
    if u is None:
        limiteur_connexion.echec(ip)
        # Message volontairement identique pour un e-mail inconnu et un mot de
        # passe faux : préciser lequel révélerait quelles adresses ont un compte.
        raise HTTPException(401, "Adresse e-mail ou mot de passe incorrect")

    limiteur_connexion.succes(ip)
    poser_cookie(reponse, creer_jeton(u.id), request)
    logger.info(f"Connexion à la console : {u.email}")
    return {"etat": "connecte", "nom": u.nom, "email": u.email}


@routeur.post("/deconnexion")
async def deconnexion(reponse: Response):
    retirer_cookie(reponse)
    return {"etat": "deconnecte"}


@routeur.get("/utilisateurs", dependencies=[Depends(verifier_jeton)])
async def lister_utilisateurs():
    async with Session() as session:
        r = await session.execute(select(Utilisateur).order_by(Utilisateur.id))
        gens = list(r.scalars())
    return {
        "utilisateurs": [
            {
                "id": u.id, "email": u.email, "nom": u.nom, "actif": u.actif is not False,
                "dernier_acces": u.dernier_acces.isoformat() if u.dernier_acces else None,
            }
            for u in gens
        ]
    }


@routeur.post("/utilisateurs", dependencies=[Depends(verifier_jeton)])
async def ajouter_utilisateur(corps: dict):
    identifiant = await creer_utilisateur(
        email=corps.get("email", ""),
        nom=corps.get("nom", ""),
        mot_de_passe=corps.get("mot_de_passe", ""),
    )
    return {"id": identifiant}


@routeur.patch("/utilisateurs/{identifiant}", dependencies=[Depends(verifier_jeton)])
async def activer_utilisateur(
    identifiant: int, corps: dict, utilisateur=Depends(utilisateur_courant)
):
    """
    Coupe (ou rend) l'accès d'un compte.

    Indispensable au départ d'un salarié : la console donne accès aux
    conversations de tous les clients. Comme les sessions sont sans état, la
    coupure est vérifiée à chaque requête — elle prend effet immédiatement, et
    n'attend pas l'expiration du cookie.
    """
    actif = bool(corps.get("actif", True))
    courriel = await definir_activation(identifiant, actif, utilisateur.id)
    logger.info(
        f"Accès {'rendu à' if actif else 'retiré à'} {courriel} par {utilisateur.email}"
    )
    return {"id": identifiant, "actif": actif}


@routeur.post("/motdepasse", dependencies=[Depends(verifier_jeton)])
async def modifier_mot_de_passe(corps: dict, utilisateur=Depends(utilisateur_courant)):
    """Changement de son propre mot de passe."""
    await changer_mot_de_passe(
        utilisateur.id, corps.get("ancien", ""), corps.get("nouveau", "")
    )
    return {"statut": "modifie"}


@routeur.get("/")
async def page():
    return FileResponse(Path(__file__).resolve().parent.parent / "simulateur" / "admin.html")


@routeur.get("/conversations", dependencies=[Depends(verifier_jeton)])
async def conversations():
    """Liste des conversations, la plus récente d'abord."""
    async with Session() as session:
        r = await session.execute(
            select(
                Message.telephone,
                func.count(Message.id).label("nombre"),
                func.max(Message.cree_le).label("dernier"),
            )
            .group_by(Message.telephone)
            .order_by(func.max(Message.cree_le).desc())
            .limit(100)
        )
        lignes = r.all()
        from agent.memory import Contact, ConversationEnPause, Escalade

        en_pause = {
            c.identifiant for c in (await session.execute(select(ConversationEnPause))).scalars()
        }
        contacts = {c.identifiant: c for c in (await session.execute(select(Contact))).scalars()}
        attente = {
            e.identifiant
            for e in (
                await session.execute(select(Escalade).where(Escalade.statut == "en_attente"))
            ).scalars()
        }

    return {
        "conversations": [
            {
                "identifiant": l.telephone,
                "nom": (contacts[l.telephone].nom if l.telephone in contacts else "") or "",
                "initiales": contacts[l.telephone].initiales() if l.telephone in contacts else "?",
                "affichage": masquer_identifiant(l.telephone),
                "messages": l.nombre,
                "dernier": l.dernier.isoformat() if l.dernier else None,
                "en_pause": l.telephone in en_pause,
                "attend_reponse": l.telephone in attente,
            }
            for l in lignes
        ]
    }


@routeur.get("/conversations/{identifiant}", dependencies=[Depends(verifier_jeton)])
async def conversation(identifiant: str):
    async with Session() as session:
        r = await session.execute(
            select(Message).where(Message.telephone == identifiant).order_by(Message.id)
        )
        messages = list(r.scalars())
    return {
        "identifiant": identifiant,
        "affichage": masquer_identifiant(identifiant),
        "contact": _contact_json(await voir_contact(identifiant), identifiant),
        "en_pause": await est_en_pause(identifiant),
        "messages": [
            {
                "role": m.role,
                "contenu": m.contenu,
                "auteur": m.auteur or ("client" if m.role == "user" else "agent"),
                "valide_par": m.valide_par or "",
                "date": m.cree_le.isoformat(),
            }
            for m in messages
        ],
    }


@routeur.post("/conversations/{identifiant}/pause", dependencies=[Depends(verifier_jeton)])
async def pause(identifiant: str, corps: dict):
    """Met l'agent en pause (ou le réactive) sur cette conversation."""
    valeur = bool(corps.get("en_pause", True))
    await basculer_pause(identifiant, valeur)
    logger.info(
        f"Agent {'mis en pause' if valeur else 'réactivé'} sur {masquer_identifiant(identifiant)}"
    )
    return {"identifiant": identifiant, "en_pause": valeur}


@routeur.post("/conversations/{identifiant}/repondre", dependencies=[Depends(verifier_jeton)])
async def repondre(
    identifiant: str, corps: dict, request: Request,
    utilisateur=Depends(utilisateur_courant),
):
    """
    Envoie un message écrit par un humain, et l'inscrit dans l'historique.

    L'inscrire est important à deux titres : sans ça l'agent reprendrait la
    conversation sans savoir ce que le collègue vient de dire au client, et
    surtout le message serait attribué à l'IA. Or on doit pouvoir dire de chaque
    message s'il a été rédigé par une machine ou par une personne — c'est la
    traçabilité qu'exige l'article 50 de l'AI Act, et c'est aussi la seule façon
    de savoir qui a répondu quoi dans une équipe.
    """
    texte = (corps.get("texte") or "").strip()
    if not texte:
        raise HTTPException(400, "Message vide")

    fournisseur = getattr(request.app.state, "fournisseur", None)
    if fournisseur is None:
        raise HTTPException(503, "Aucun fournisseur WhatsApp configuré")

    envoye = await fournisseur.envoyer_message(identifiant, texte, {})
    if not envoye:
        raise HTTPException(502, "Le fournisseur a refusé l'envoi")

    await enregistrer_message(
        identifiant, "assistant", texte, auteur="humain", valide_par=utilisateur.email
    )
    logger.info(
        f"Message humain envoyé à {masquer_identifiant(identifiant)} par {utilisateur.email}"
    )
    return {"statut": "envoye"}


@routeur.delete("/conversations/{identifiant}", dependencies=[Depends(verifier_jeton)])
async def effacer(identifiant: str):
    """Droit à l'effacement (RGPD art. 17)."""
    from agent.memory import effacer_historique

    n = await effacer_historique(identifiant)
    return {"identifiant": identifiant, "messages_effaces": n}


# ═════════════════════════════════════════════════════════════════════════
# Gestion du contenu : prompt, documents, consignes, demandes
# ═════════════════════════════════════════════════════════════════════════

import re
from datetime import timedelta

import yaml

from agent.memory import Consigne, Demande, Message as MessageDB

DOSSIER_KNOWLEDGE = Path("knowledge")
FICHIER_PROMPTS = Path("config/prompts.yaml")
EXTENSIONS_AUTORISEES = {".md", ".txt", ".csv", ".json", ".yaml", ".yml"}

# Plafonds d'écriture. Sans eux, un envoi répété remplit le disque du serveur —
# et sur un hébergeur, un disque plein arrête l'agent sans message clair.
# 512 Ko pour un document, c'est déjà un tarif de plusieurs centaines de pages ;
# tout ce qui dépasse relève du fichier joint, pas d'un texte à donner au modèle.
MAX_OCTETS_DOCUMENT = 512 * 1024
MAX_OCTETS_PROMPT = 64 * 1024
MAX_DOCUMENTS = 200


def _verifier_taille(texte: str, maximum: int, quoi: str) -> str:
    octets = len(texte.encode("utf-8"))
    if octets > maximum:
        raise HTTPException(
            413,
            f"{quoi} trop volumineux : {octets // 1024} Ko pour un maximum de "
            f"{maximum // 1024} Ko. Découpez-le en plusieurs fichiers.",
        )
    return texte


def _nom_de_fichier_sur(nom: str) -> str:
    """
    Neutralise toute tentative de sortir du dossier knowledge/.

    Sans ça, un nom comme « ../../.env » permettrait de lire ou d'écraser
    n'importe quel fichier du serveur depuis l'interface.
    """
    nom = Path(nom).name  # supprime tout composant de chemin
    if not nom or nom.startswith("."):
        raise HTTPException(400, "Nom de fichier invalide")
    if Path(nom).suffix.lower() not in EXTENSIONS_AUTORISEES:
        raise HTTPException(
            400,
            f"Extension refusée. Autorisées : {', '.join(sorted(EXTENSIONS_AUTORISEES))}",
        )
    if not re.fullmatch(r"[A-Za-z0-9 ._-]{1,80}", nom):
        raise HTTPException(400, "Nom de fichier invalide")
    return nom


# ── Prompt système ───────────────────────────────────────────────────────


@routeur.get("/prompts", dependencies=[Depends(verifier_jeton)])
async def lire_prompts():
    try:
        donnees = yaml.safe_load(FICHIER_PROMPTS.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        donnees = {}
    return {
        "system_prompt": donnees.get("system_prompt", ""),
        "fallback_message": donnees.get("fallback_message", ""),
        "error_message": donnees.get("error_message", ""),
        "quota_message": donnees.get("quota_message", ""),
    }


@routeur.put("/prompts", dependencies=[Depends(verifier_jeton)])
async def ecrire_prompts(corps: dict):
    """
    Réécrit config/prompts.yaml.

    Une sauvegarde horodatée est conservée : le prompt système est le cœur du
    comportement de l'agent, une mauvaise manipulation doit être réversible.
    """
    if not (corps.get("system_prompt") or "").strip():
        raise HTTPException(400, "Le prompt système ne peut pas être vide")
    _verifier_taille(corps["system_prompt"], MAX_OCTETS_PROMPT, "Le prompt système")

    FICHIER_PROMPTS.parent.mkdir(exist_ok=True)
    if FICHIER_PROMPTS.exists():
        sauvegarde = FICHIER_PROMPTS.with_suffix(
            f".{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.bak"
        )
        sauvegarde.write_text(FICHIER_PROMPTS.read_text(encoding="utf-8"), encoding="utf-8")
        # On ne garde que les dix dernières : une sauvegarde par enregistrement,
        # conservée à vie, finit par saturer le disque d'un petit serveur.
        anciennes = sorted(FICHIER_PROMPTS.parent.glob("prompts.*.bak"))
        for vieille in anciennes[:-10]:
            vieille.unlink(missing_ok=True)

    donnees = {
        "system_prompt": corps["system_prompt"],
        "fallback_message": corps.get("fallback_message") or "Désolé, je n'ai pas bien compris.",
        "error_message": corps.get("error_message") or "Désolé, problème technique.",
        "quota_message": corps.get("quota_message") or "Limite atteinte, l'équipe vous répondra.",
    }
    FICHIER_PROMPTS.write_text(
        yaml.dump(donnees, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    logger.info("Prompt système modifié depuis le back-office")
    return {"statut": "enregistre"}


# ── Documents métier ─────────────────────────────────────────────────────


@routeur.get("/documents", dependencies=[Depends(verifier_jeton)])
async def lister_documents():
    DOSSIER_KNOWLEDGE.mkdir(exist_ok=True)
    fichiers = []
    for c in sorted(DOSSIER_KNOWLEDGE.iterdir()):
        if c.name.startswith(".") or not c.is_file():
            continue
        fichiers.append(
            {
                "nom": c.name,
                "octets": c.stat().st_size,
                "modifie": datetime.fromtimestamp(c.stat().st_mtime, timezone.utc).isoformat(),
                "lisible": c.suffix.lower() in EXTENSIONS_AUTORISEES,
            }
        )
    return {"documents": fichiers}


@routeur.get("/documents/{nom}", dependencies=[Depends(verifier_jeton)])
async def lire_document(nom: str):
    chemin = DOSSIER_KNOWLEDGE / _nom_de_fichier_sur(nom)
    if not chemin.is_file():
        raise HTTPException(404, "Document introuvable")
    return {"nom": chemin.name, "contenu": chemin.read_text(encoding="utf-8")}


@routeur.put("/documents/{nom}", dependencies=[Depends(verifier_jeton)])
async def ecrire_document(nom: str, corps: dict):
    contenu = _verifier_taille(
        corps.get("contenu", "") or "", MAX_OCTETS_DOCUMENT, "Le document"
    )
    chemin = DOSSIER_KNOWLEDGE / _nom_de_fichier_sur(nom)
    DOSSIER_KNOWLEDGE.mkdir(exist_ok=True)

    if not chemin.exists():
        existants = sum(1 for c in DOSSIER_KNOWLEDGE.iterdir() if c.is_file())
        if existants >= MAX_DOCUMENTS:
            raise HTTPException(
                413, f"Limite de {MAX_DOCUMENTS} documents atteinte. Supprimez-en avant d'en ajouter."
            )

    chemin.write_text(contenu, encoding="utf-8")
    logger.info(f"Document modifié depuis le back-office : {chemin.name}")
    return {"statut": "enregistre", "nom": chemin.name}


@routeur.delete("/documents/{nom}", dependencies=[Depends(verifier_jeton)])
async def supprimer_document(nom: str):
    chemin = DOSSIER_KNOWLEDGE / _nom_de_fichier_sur(nom)
    if not chemin.is_file():
        raise HTTPException(404, "Document introuvable")
    chemin.unlink()
    logger.info(f"Document supprimé depuis le back-office : {chemin.name}")
    return {"statut": "supprime"}


# ── Consignes ────────────────────────────────────────────────────────────


def _date_ou_none(valeur: str | None) -> datetime | None:
    if not valeur:
        return None
    try:
        d = datetime.fromisoformat(valeur)
    except ValueError:
        raise HTTPException(400, f"Date invalide : {valeur}")
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


@routeur.get("/consignes", dependencies=[Depends(verifier_jeton)])
async def lister_consignes():
    async with Session() as session:
        r = await session.execute(select(Consigne).order_by(Consigne.id.desc()))
        consignes = list(r.scalars())
    return {
        "consignes": [
            {
                "id": c.id,
                "texte": c.texte,
                "debut": c.debut.isoformat() if c.debut else None,
                "fin": c.fin.isoformat() if c.fin else None,
                "activee": c.activee is not False,
                "statut": c.statut(),
            }
            for c in consignes
        ]
    }


@routeur.post("/consignes", dependencies=[Depends(verifier_jeton)])
async def creer_consigne(corps: dict):
    texte = (corps.get("texte") or "").strip()
    if not texte:
        raise HTTPException(400, "La consigne ne peut pas être vide")

    debut, fin = _date_ou_none(corps.get("debut")), _date_ou_none(corps.get("fin"))
    if debut and fin and fin <= debut:
        raise HTTPException(400, "La date de fin doit suivre la date de début")

    async with Session() as session:
        c = Consigne(texte=texte, debut=debut, fin=fin, activee=True)
        session.add(c)
        await session.commit()
        await session.refresh(c)
        resultat = {"id": c.id, "statut": c.statut()}
    logger.info(f"Consigne #{resultat['id']} créée : {texte[:60]}")
    return resultat


@routeur.patch("/consignes/{identifiant}", dependencies=[Depends(verifier_jeton)])
async def modifier_consigne(identifiant: int, corps: dict):
    async with Session() as session:
        c = await session.get(Consigne, identifiant)
        if c is None:
            raise HTTPException(404, "Consigne introuvable")
        if "activee" in corps:
            c.activee = bool(corps["activee"])
        if "texte" in corps and corps["texte"].strip():
            c.texte = corps["texte"].strip()
        if "debut" in corps:
            c.debut = _date_ou_none(corps["debut"])
        if "fin" in corps:
            c.fin = _date_ou_none(corps["fin"])
        await session.commit()
        await session.refresh(c)
        return {"id": c.id, "statut": c.statut(), "activee": c.activee is not False}


@routeur.delete("/consignes/{identifiant}", dependencies=[Depends(verifier_jeton)])
async def supprimer_consigne(identifiant: int):
    async with Session() as session:
        await session.execute(delete(Consigne).where(Consigne.id == identifiant))
        await session.commit()
    return {"statut": "supprime"}


# ── Demandes ─────────────────────────────────────────────────────────────


@routeur.get("/demandes", dependencies=[Depends(verifier_jeton)])
async def lister_demandes(statut: str | None = None):
    async with Session() as session:
        requete = select(Demande).order_by(Demande.id.desc()).limit(200)
        if statut:
            requete = requete.where(Demande.statut == statut)
        demandes = list((await session.execute(requete)).scalars())
    return {
        "demandes": [
            {
                "id": d.id,
                "identifiant": d.identifiant,
                "affichage": masquer_identifiant(d.identifiant),
                "nom_client": d.nom_client,
                "details": d.details,
                "date_souhaitee": d.date_souhaitee,
                "statut": d.statut,
                "cree_le": d.cree_le.isoformat(),
            }
            for d in demandes
        ]
    }


@routeur.patch("/demandes/{identifiant}", dependencies=[Depends(verifier_jeton)])
async def modifier_demande(identifiant: int, corps: dict):
    statut = corps.get("statut")
    if statut not in ("a_traiter", "traitee", "annulee"):
        raise HTTPException(400, "Statut invalide")
    async with Session() as session:
        d = await session.get(Demande, identifiant)
        if d is None:
            raise HTTPException(404, "Demande introuvable")
        d.statut = statut
        await session.commit()
    return {"id": identifiant, "statut": statut}


# ── Fiche client ─────────────────────────────────────────────────────────


@routeur.get("/conversations/{identifiant}/fiche", dependencies=[Depends(verifier_jeton)])
async def fiche_client(identifiant: str):
    """
    Synthèse de la conversation, produite par Claude.

    Ce n'est pas une donnée stockée : elle est recalculée à la demande, pour
    éviter de conserver un profil client figé qu'il faudrait ensuite maintenir
    et purger séparément.
    """
    from agent.brain import client_llm

    async with Session() as session:
        r = await session.execute(
            select(MessageDB).where(MessageDB.telephone == identifiant).order_by(MessageDB.id)
        )
        messages = list(r.scalars())

    if not messages:
        raise HTTPException(404, "Aucune conversation à synthétiser")

    transcription = "\n".join(
        f"{'Client' if m.role == 'user' else 'Agent'} : {m.contenu}" for m in messages
    )

    # On passe par la couche commune aux fournisseurs, et non par le client
    # Anthropic en direct : sinon la synthèse est la seule fonction du produit
    # qui exige Anthropic, et elle casse chez qui a choisi OpenAI ou Google.
    consigne = (
        "Tu produis une fiche de synthèse à usage interne, destinée à l'équipe "
        "d'une entreprise. Réponds en français, en texte brut, sans markdown.\n\n"
        "Structure imposée, exactement ces quatre sections :\n"
        "DEMANDE : ce que le client veut, en une ou deux phrases.\n"
        "ÉLÉMENTS RECUEILLIS : la liste des informations déjà obtenues "
        "(nom, date, produit, quantité…), une par ligne.\n"
        "MANQUANTS : ce qu'il reste à obtenir pour conclure, une par ligne. "
        "Écris « rien » si le dossier est complet.\n"
        "À FAIRE : l'action concrète attendue de l'équipe, en une phrase.\n\n"
        "N'invente rien. Si une information est absente, ne la déduis pas."
    )

    async def _aucun_outil(nom: str, arguments: dict) -> str:  # pragma: no cover
        return "Aucun outil disponible pour la synthèse."

    try:
        bilan = await client_llm().converser(
            systeme=consigne,
            historique=[],
            message=transcription,
            outils=[],
            executer=_aucun_outil,
            max_tours=1,
        )
    except Exception as e:  # noqa: BLE001
        logger.error(f"Fiche client impossible : {e}")
        raise HTTPException(502, "La synthèse a échoué")

    texte = bilan.texte
    return {
        "identifiant": identifiant,
        "affichage": masquer_identifiant(identifiant),
        "nombre_messages": len(messages),
        "synthese": texte.strip(),
    }


# ═════════════════════════════════════════════════════════════════════════
# Contacts et escalades
# ═════════════════════════════════════════════════════════════════════════

from agent.memory import (  # noqa: E402
    Contact,
    Escalade,
    modifier_contact,
    voir_contact,
)


def _contact_json(c: Contact | None, identifiant: str) -> dict:
    if c is None:
        return {
            "identifiant": identifiant, "nom": "", "nom_whatsapp": "", "nom_affiche": "",
            "username": "", "pays": "", "notes": "", "initiales": "?",
        }
    return {
        "identifiant": c.identifiant, "nom": c.nom, "nom_whatsapp": c.nom_whatsapp,
        "nom_affiche": c.nom_affiche, "username": c.username, "pays": c.pays,
        "notes": c.notes, "initiales": c.initiales(),
        "premier_contact": c.premier_contact.isoformat() if c.premier_contact else None,
    }


@routeur.get("/contacts/{identifiant}", dependencies=[Depends(verifier_jeton)])
async def lire_contact(identifiant: str):
    return _contact_json(await voir_contact(identifiant), identifiant)


@routeur.put("/contacts/{identifiant}", dependencies=[Depends(verifier_jeton)])
async def ecrire_contact(identifiant: str, corps: dict):
    await modifier_contact(identifiant, corps.get("nom_affiche"), corps.get("notes"))
    return _contact_json(await voir_contact(identifiant), identifiant)


@routeur.get("/escalades", dependencies=[Depends(verifier_jeton)])
async def lister_escalades(statut: str = "en_attente"):
    async with Session() as session:
        requete = select(Escalade).order_by(Escalade.id.desc()).limit(100)
        if statut != "toutes":
            requete = requete.where(Escalade.statut == statut)
        escalades = list((await session.execute(requete)).scalars())
        contacts = {
            c.identifiant: c
            for c in (await session.execute(select(Contact))).scalars()
        }
    return {
        "escalades": [
            {
                "id": e.id,
                "identifiant": e.identifiant,
                "nom": (contacts.get(e.identifiant).nom if contacts.get(e.identifiant) else "")
                or masquer_identifiant(e.identifiant),
                "motif": e.motif,
                "question_equipe": e.question_equipe,
                "reponse_proposee": e.reponse_proposee,
                "urgence": e.urgence,
                "statut": e.statut,
                "traite_par": e.traite_par,
                "cree_le": e.cree_le.isoformat(),
            }
            for e in escalades
        ]
    }


@routeur.post("/escalades/{identifiant}/repondre", dependencies=[Depends(verifier_jeton)])
async def repondre_escalade(
    identifiant: int, corps: dict, request: Request,
    utilisateur=Depends(utilisateur_courant),
):
    """
    Valide (ou corrige) le brouillon de l'agent et l'envoie au client.

    Le message part signé du nom de la personne qui l'a validé : l'AI Act lève
    l'obligation de marquage dès lors qu'un humain endosse la responsabilité
    éditoriale, et il faut pouvoir prouver qui l'a endossée.
    """
    texte = (corps.get("texte") or "").strip()
    if not texte:
        raise HTTPException(400, "Le message ne peut pas être vide")

    async with Session() as session:
        e = await session.get(Escalade, identifiant)
        if e is None:
            raise HTTPException(404, "Escalade introuvable")
        if e.statut != "en_attente":
            raise HTTPException(409, "Cette escalade a déjà été traitée")
        cible = e.identifiant

    fournisseur = getattr(request.app.state, "fournisseur", None)
    if fournisseur is None:
        raise HTTPException(503, "Aucun fournisseur WhatsApp configuré")
    if not await fournisseur.envoyer_message(cible, texte, {}):
        raise HTTPException(502, "Le fournisseur a refusé l'envoi")

    modifie = texte.strip() != (corps.get("brouillon") or "").strip()
    await enregistrer_message(
        cible, "assistant", texte,
        auteur="humain", valide_par=utilisateur.email,
    )

    async with Session() as session:
        e = await session.get(Escalade, identifiant)
        e.statut = "repondue"
        e.traite_par = utilisateur.email
        e.traite_le = datetime.now(timezone.utc)
        await session.commit()

    logger.info(
        f"Escalade #{identifiant} {'corrigée puis' if modifie else 'validée telle quelle,'} "
        f"envoyée par {utilisateur.email}"
    )
    return {"statut": "envoye", "brouillon_modifie": modifie}


@routeur.post("/escalades/{identifiant}/ignorer", dependencies=[Depends(verifier_jeton)])
async def ignorer_escalade(identifiant: int, utilisateur=Depends(utilisateur_courant)):
    """Classe l'escalade sans envoyer de message et rend la main à l'agent."""
    async with Session() as session:
        e = await session.get(Escalade, identifiant)
        if e is None:
            raise HTTPException(404, "Escalade introuvable")
        e.statut = "ignoree"
        e.traite_par = utilisateur.email
        e.traite_le = datetime.now(timezone.utc)
        cible = e.identifiant
        await session.commit()
    await basculer_pause(cible, False)
    return {"statut": "ignoree"}


# ═════════════════════════════════════════════════════════════════════════
# Marque du client
# ═════════════════════════════════════════════════════════════════════════

FICHIER_MARQUE = Path("config/marque.yaml")
IMAGES_AUTORISEES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                     ".svg": "image/svg+xml", ".webp": "image/webp"}


def lire_marque() -> dict:
    """
    Identité affichée dans la console : nom de l'espace et logo.

    Se configure par fichier plutôt que par l'interface : c'est l'intégrateur
    qui pose le logo au moment de l'installation, pas le commerçant au
    quotidien. Un fichier se donne à un agent de développement ; un formulaire
    d'envoi de fichier, non.
    """
    try:
        donnees = yaml.safe_load(FICHIER_MARQUE.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        donnees = {}

    nom_logo = str(donnees.get("logo") or "").strip()
    chemin = None
    if nom_logo:
        candidat = Path("config") / Path(nom_logo).name
        if candidat.is_file() and candidat.suffix.lower() in IMAGES_AUTORISEES:
            chemin = candidat

    return {
        "nom": str(donnees.get("nom") or "").strip(),
        "logo": chemin.name if chemin else "",
        "_chemin": chemin,
    }


@routeur.get("/marque", dependencies=[Depends(verifier_jeton)])
async def marque():
    m = lire_marque()
    return {"nom": m["nom"], "a_logo": bool(m["logo"])}


@routeur.get("/logo", dependencies=[Depends(verifier_jeton)])
async def logo():
    m = lire_marque()
    if not m["_chemin"]:
        raise HTTPException(404, "Aucun logo configuré")
    return FileResponse(
        m["_chemin"], media_type=IMAGES_AUTORISEES[m["_chemin"].suffix.lower()]
    )
