"""
Mémoire des conversations et déduplication des événements.

Deux tables :
  - messages          : l'historique par client, pour que l'agent ait du contexte
  - evenements_traites : garantit qu'un même webhook rejoué n'est traité qu'une fois
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from sqlalchemy import (
    DateTime,
    Integer,
    String,
    Text,
    delete,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from agent.securite import masquer_telephone

load_dotenv()
logger = logging.getLogger("agentkit")

MAX_MESSAGES_HISTORIQUE = int(os.getenv("MAX_MESSAGES_HISTORIQUE", "20") or "20")
RETENTION_JOURS = int(os.getenv("RETENTION_JOURS", "90") or "90")


def _url_base() -> str:
    """
    Normalise l'URL de base.

    Les hébergeurs fournissent PostgreSQL en « postgresql:// » ou « postgres:// » ;
    SQLAlchemy en mode asynchrone exige un pilote explicite.
    """
    url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./agentkit.db").strip()
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


URL_BASE = _url_base()

if URL_BASE.startswith("sqlite") and os.getenv("ENVIRONMENT", "").lower() == "production":
    logger.warning(
        "⚠️  SQLite en production : le disque du conteneur est éphémère. "
        "Chaque redéploiement effacera TOUT l'historique. Utilisez PostgreSQL."
    )

moteur = create_async_engine(URL_BASE, echo=False, future=True)
Session = async_sessionmaker(moteur, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telephone: Mapped[str] = mapped_column(String(32), index=True)
    role: Mapped[str] = mapped_column(String(16))
    contenu: Mapped[str] = mapped_column(Text)
    cree_le: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class EvenementTraite(Base):
    __tablename__ = "evenements_traites"

    evenement_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    cree_le: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


async def initialiser_base() -> None:
    async with moteur.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Base de données prête")


async def marquer_evenement_traite(evenement_id: str) -> bool:
    """
    Retourne True si l'événement est nouveau, False s'il a déjà été traité.

    On s'appuie sur la contrainte de clé primaire plutôt que sur un SELECT puis
    un INSERT : deux livraisons simultanées du même webhook passeraient le
    SELECT toutes les deux et le client recevrait deux réponses.
    """
    if not evenement_id:
        return True
    try:
        async with Session() as session:
            session.add(EvenementTraite(evenement_id=evenement_id))
            await session.commit()
        return True
    except IntegrityError:
        return False


async def liberer_evenement(evenement_id: str) -> None:
    """
    Retire la marque d'un événement dont le traitement a échoué.

    Sans ça, la nouvelle tentative du fournisseur serait écartée comme doublon
    et le client resterait définitivement sans réponse.
    """
    if not evenement_id:
        return
    async with Session() as session:
        await session.execute(
            delete(EvenementTraite).where(EvenementTraite.evenement_id == evenement_id)
        )
        await session.commit()


async def nettoyer_evenements_anciens(jours: int = 7) -> int:
    limite = datetime.now(timezone.utc) - timedelta(days=jours)
    async with Session() as session:
        r = await session.execute(
            delete(EvenementTraite).where(EvenementTraite.cree_le < limite)
        )
        await session.commit()
    return r.rowcount or 0


async def purger_donnees_expirees() -> int:
    """
    Efface les conversations au-delà de la durée de conservation (RGPD).

    Le kit d'origine gardait l'historique indéfiniment. Conserver des échanges
    clients sans limite n'est pas défendable : on fixe une durée par défaut et
    on l'applique automatiquement au démarrage.
    """
    if RETENTION_JOURS <= 0:
        return 0
    limite = datetime.now(timezone.utc) - timedelta(days=RETENTION_JOURS)
    async with Session() as session:
        r = await session.execute(delete(Message).where(Message.cree_le < limite))
        await session.commit()
    n = r.rowcount or 0
    if n:
        logger.info(f"Purge RGPD : {n} message(s) de plus de {RETENTION_JOURS} jours effacés")
    return n


async def enregistrer_message(telephone: str, role: str, contenu: str) -> None:
    async with Session() as session:
        session.add(Message(telephone=telephone, role=role, contenu=contenu))
        await session.commit()


async def obtenir_historique(telephone: str) -> list[dict]:
    """Derniers échanges de CE client, du plus ancien au plus récent."""
    async with Session() as session:
        r = await session.execute(
            select(Message)
            .where(Message.telephone == telephone)
            .order_by(Message.id.desc())
            .limit(MAX_MESSAGES_HISTORIQUE)
        )
        lignes = list(r.scalars())
    lignes.reverse()
    return [{"role": m.role, "content": m.contenu} for m in lignes]


async def effacer_historique(telephone: str) -> int:
    """Droit à l'effacement (RGPD art. 17) pour un client donné."""
    async with Session() as session:
        r = await session.execute(delete(Message).where(Message.telephone == telephone))
        await session.commit()
    logger.info(f"Historique effacé pour {masquer_telephone(telephone)}")
    return r.rowcount or 0
