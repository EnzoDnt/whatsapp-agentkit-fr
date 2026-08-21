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

import logging
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import DateTime, String, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from agent.memory import Base, Message, Session, enregistrer_message
from agent.securite import masquer_identifiant

logger = logging.getLogger("agentkit")

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()


class ConversationEnPause(Base):
    """Conversations où un humain a repris la main : l'agent se tait."""

    __tablename__ = "conversations_en_pause"

    identifiant: Mapped[str] = mapped_column(String(256), primary_key=True)
    depuis: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


async def est_en_pause(identifiant: str) -> bool:
    async with Session() as session:
        r = await session.execute(
            select(ConversationEnPause).where(ConversationEnPause.identifiant == identifiant)
        )
        return r.scalar_one_or_none() is not None


async def basculer_pause(identifiant: str, en_pause: bool) -> bool:
    async with Session() as session:
        if en_pause:
            try:
                session.add(ConversationEnPause(identifiant=identifiant))
                await session.commit()
            except IntegrityError:
                pass
        else:
            await session.execute(
                delete(ConversationEnPause).where(ConversationEnPause.identifiant == identifiant)
            )
            await session.commit()
    return en_pause


def verifier_jeton(request: Request) -> None:
    """compare_digest et non « == » : évite de fuiter le jeton par le temps de réponse."""
    fourni = request.headers.get("X-Admin-Token") or request.query_params.get("jeton") or ""
    if not ADMIN_TOKEN or not secrets.compare_digest(fourni, ADMIN_TOKEN):
        raise HTTPException(401, "Jeton d'administration invalide")


routeur = APIRouter(prefix="/admin", tags=["admin"])


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
        en_pause = {
            c.identifiant for c in (await session.execute(select(ConversationEnPause))).scalars()
        }

    return {
        "conversations": [
            {
                "identifiant": l.telephone,
                "affichage": masquer_identifiant(l.telephone),
                "messages": l.nombre,
                "dernier": l.dernier.isoformat() if l.dernier else None,
                "en_pause": l.telephone in en_pause,
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
        "en_pause": await est_en_pause(identifiant),
        "messages": [
            {"role": m.role, "contenu": m.contenu, "date": m.cree_le.isoformat()}
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
async def repondre(identifiant: str, corps: dict, request: Request):
    """
    Envoie un message écrit par un humain, et l'inscrit dans l'historique.

    L'inscrire est important : sans ça, l'agent reprendrait la conversation
    sans savoir ce que le collègue vient de dire au client.
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

    await enregistrer_message(identifiant, "assistant", texte)
    logger.info(f"Message humain envoyé à {masquer_identifiant(identifiant)}")
    return {"statut": "envoye"}


@routeur.delete("/conversations/{identifiant}", dependencies=[Depends(verifier_jeton)])
async def effacer(identifiant: str):
    """Droit à l'effacement (RGPD art. 17)."""
    from agent.memory import effacer_historique

    n = await effacer_historique(identifiant)
    return {"identifiant": identifiant, "messages_effaces": n}
