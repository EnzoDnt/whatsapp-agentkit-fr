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
    Boolean,
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
from agent.environnement import est_production

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

if URL_BASE.startswith("sqlite") and est_production():
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
    role: Mapped[str] = mapped_column(String(16))   # "user" | "assistant" — pour le modèle
    contenu: Mapped[str] = mapped_column(Text)
    # Qui a réellement écrit : "client", "agent" (IA), "humain" (l'équipe).
    # `role` sert à parler au modèle ; `auteur` sert à la traçabilité et à
    # l'obligation de transparence de l'AI Act (art. 50) : on doit pouvoir dire
    # de chaque message s'il a été produit par une IA.
    auteur: Mapped[str] = mapped_column(String(16), default="agent")
    # Renseigné quand une personne a relu et validé un brouillon de l'IA. L'AI Act
    # lève l'obligation de marquage dès lors qu'un humain endosse la
    # responsabilité éditoriale : c'est ce que trace ce champ.
    valide_par: Mapped[str] = mapped_column(String(200), default="")
    cree_le: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class EvenementTraite(Base):
    __tablename__ = "evenements_traites"

    evenement_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    cree_le: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class Consigne(Base):
    """
    Une consigne ponctuelle donnée à l'agent, comme on briefe un employé.

    « Plus de tarte au citron du 15 au 25 août », « proposer la promo de la
    rentrée », « ne pas prendre de commande pour le 14 juillet ». L'agent les
    reçoit dans son prompt système, mais seulement pendant leur période de
    validité — au-delà, elles disparaissent d'elles-mêmes.

    Une consigne sans date de fin reste active jusqu'à désactivation manuelle.
    """

    __tablename__ = "consignes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    texte: Mapped[str] = mapped_column(Text)
    debut: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fin: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activee: Mapped[bool] = mapped_column(Boolean, default=True)
    cree_le: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def est_active(self, maintenant: datetime | None = None) -> bool:
        # `activee` vaut None tant que l'objet n'est pas persisté : le défaut
        # SQLAlchemy s'applique à l'insertion, pas à la construction. On traite
        # donc None comme « activée ».
        m = maintenant or datetime.now(timezone.utc)
        if self.activee is False:
            return False
        if self.debut and _aware(self.debut) > m:
            return False
        if self.fin and _aware(self.fin) < m:
            return False
        return True

    def statut(self) -> str:
        m = datetime.now(timezone.utc)
        if self.activee is False:
            return "desactivee"
        if self.debut and _aware(self.debut) > m:
            return "programmee"
        if self.fin and _aware(self.fin) < m:
            return "expiree"
        return "active"


class Demande(Base):
    """
    Une demande client enregistrée par l'agent : commande, réservation, RDV.

    Volontairement générique — le champ `details` est du texte libre, ce qui
    permet au même outil de servir une boulangerie, un cabinet dentaire ou un
    garage sans changer le schéma.
    """

    __tablename__ = "demandes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    identifiant: Mapped[str] = mapped_column(String(256), index=True)
    nom_client: Mapped[str] = mapped_column(String(200))
    details: Mapped[str] = mapped_column(Text)
    date_souhaitee: Mapped[str] = mapped_column(String(100), default="")
    statut: Mapped[str] = mapped_column(String(20), default="a_traiter")
    cree_le: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class Escalade(Base):
    """
    L'agent passe la main : il ne répond pas et sollicite l'équipe.

    Trois déclencheurs, alignés sur ce que font les plateformes établies :
    le client demande explicitement un humain, il montre de l'agacement, ou
    l'agent n'a pas l'information et refuse d'inventer.

    L'agent joint sa question ET un brouillon de réponse. La personne valide,
    corrige, ou écrit la sienne. Un brouillon validé par un humain engage la
    responsabilité éditoriale de celui-ci — ce que l'AI Act reconnaît
    explicitement comme levant l'obligation de marquage automatique.
    """

    __tablename__ = "escalades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    identifiant: Mapped[str] = mapped_column(String(256), index=True)
    motif: Mapped[str] = mapped_column(Text)
    question_equipe: Mapped[str] = mapped_column(Text, default="")
    reponse_proposee: Mapped[str] = mapped_column(Text, default="")
    urgence: Mapped[str] = mapped_column(String(16), default="normale")
    statut: Mapped[str] = mapped_column(String(16), default="en_attente", index=True)
    traite_par: Mapped[str] = mapped_column(String(200), default="")
    traite_le: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cree_le: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class Contact(Base):
    """
    Ce qu'on sait de la personne à l'autre bout.

    Meta ne transmet pas la photo de profil des clients — seuls des outils non
    officiels le font, au prix d'un bannissement du numéro. On dispose en
    revanche du nom de profil WhatsApp, du @username et du code pays.

    `nom_affiche` et `notes` appartiennent au commerçant : c'est sa fiche, il
    l'écrit comme il veut, et elle prime sur le nom déclaré par WhatsApp.
    """

    __tablename__ = "contacts"

    identifiant: Mapped[str] = mapped_column(String(256), primary_key=True)
    nom_whatsapp: Mapped[str] = mapped_column(String(200), default="")
    nom_affiche: Mapped[str] = mapped_column(String(200), default="")
    username: Mapped[str] = mapped_column(String(120), default="")
    pays: Mapped[str] = mapped_column(String(8), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    premier_contact: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    dernier_contact: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    @property
    def nom(self) -> str:
        return self.nom_affiche or self.nom_whatsapp or ""

    def initiales(self) -> str:
        source = self.nom.strip()
        if not source:
            return "?"
        morceaux = [m for m in source.split() if m]
        if len(morceaux) == 1:
            return morceaux[0][:2].upper()
        return (morceaux[0][0] + morceaux[-1][0]).upper()


async def voir_contact(identifiant: str) -> Contact | None:
    async with Session() as session:
        return await session.get(Contact, identifiant)


async def toucher_contact(
    identifiant: str, nom_whatsapp: str = "", username: str = "", pays: str = ""
) -> None:
    """Crée ou met à jour la fiche à chaque message reçu, sans écraser l'édition manuelle."""
    async with Session() as session:
        c = await session.get(Contact, identifiant)
        if c is None:
            c = Contact(identifiant=identifiant)
            session.add(c)
        if nom_whatsapp:
            c.nom_whatsapp = nom_whatsapp
        if username:
            c.username = username
        if pays:
            c.pays = pays
        c.dernier_contact = datetime.now(timezone.utc)
        await session.commit()


async def modifier_contact(identifiant: str, nom_affiche: str | None, notes: str | None) -> None:
    async with Session() as session:
        c = await session.get(Contact, identifiant)
        if c is None:
            c = Contact(identifiant=identifiant)
            session.add(c)
        if nom_affiche is not None:
            c.nom_affiche = nom_affiche.strip()[:200]
        if notes is not None:
            c.notes = notes.strip()
        await session.commit()


class ConversationEnPause(Base):
    """Conversations où un humain a repris la main : l'agent se tait."""

    __tablename__ = "conversations_en_pause"

    identifiant: Mapped[str] = mapped_column(String(256), primary_key=True)
    depuis: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


async def conversation_en_pause(identifiant: str) -> bool:
    async with Session() as session:
        r = await session.execute(
            select(ConversationEnPause).where(ConversationEnPause.identifiant == identifiant)
        )
        return r.scalar_one_or_none() is not None


async def basculer_pause_conversation(identifiant: str, en_pause: bool) -> bool:
    async with Session() as session:
        if en_pause:
            try:
                session.add(ConversationEnPause(identifiant=identifiant))
                await session.commit()
            except IntegrityError:
                await session.rollback()
        else:
            await session.execute(
                delete(ConversationEnPause).where(
                    ConversationEnPause.identifiant == identifiant
                )
            )
            await session.commit()
    return en_pause


async def enregistrer_escalade(
    identifiant: str,
    motif: str,
    question_equipe: str = "",
    reponse_proposee: str = "",
    urgence: str = "normale",
) -> int:
    async with Session() as session:
        e = Escalade(
            identifiant=identifiant,
            motif=motif,
            question_equipe=question_equipe,
            reponse_proposee=reponse_proposee,
            urgence=urgence if urgence in ("normale", "haute") else "normale",
        )
        session.add(e)
        await session.commit()
        await session.refresh(e)
        return e.id


async def escalades_en_attente(identifiant: str | None = None) -> int:
    async with Session() as session:
        requete = select(Escalade).where(Escalade.statut == "en_attente")
        if identifiant:
            requete = requete.where(Escalade.identifiant == identifiant)
        return len(list((await session.execute(requete)).scalars()))


async def migrer_colonnes() -> None:
    """
    Ajoute les colonnes manquantes aux bases existantes.

    `create_all` crée les tables absentes mais ne touche jamais à celles qui
    existent déjà. Sans ce passage, mettre à jour le kit sur une installation
    en service planterait à la première requête sur une colonne inconnue.
    """
    ajouts = {
        "messages": {
            # Pas de DEFAULT 'agent' ici : sur une base existante, ALTER TABLE
            # remplirait TOUTE la colonne avec 'agent', y compris les messages
            # entrants du client. Le rattrapage ci-dessous déduit l'auteur du
            # rôle, qui lui est fiable.
            "auteur": "VARCHAR(16) DEFAULT ''",
            "valide_par": "VARCHAR(200) DEFAULT ''",
        },
    }
    rattrapages = [
        "UPDATE messages SET auteur='client' WHERE role='user' AND (auteur IS NULL OR auteur='' OR auteur='agent')",
        "UPDATE messages SET auteur='agent' WHERE role='assistant' AND (auteur IS NULL OR auteur='')",
    ]
    async with moteur.begin() as conn:
        for table, colonnes in ajouts.items():
            try:
                res = await conn.exec_driver_sql(f"PRAGMA table_info({table})")
                existantes = {ligne[1] for ligne in res.fetchall()}
            except Exception:  # noqa: BLE001 — PostgreSQL n'a pas PRAGMA
                continue
            if not existantes:
                continue
            for nom, definition in colonnes.items():
                if nom not in existantes:
                    await conn.exec_driver_sql(
                        f"ALTER TABLE {table} ADD COLUMN {nom} {definition}"
                    )
                    logger.info(f"Migration : colonne {table}.{nom} ajoutée")

        for requete in rattrapages:
            try:
                await conn.exec_driver_sql(requete)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"Rattrapage ignoré ({e})")


def _aware(d: datetime) -> datetime:
    """SQLite rend des datetime naïfs ; on les remet en UTC pour pouvoir comparer."""
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


async def consignes_actives() -> list[Consigne]:
    """Les consignes en vigueur maintenant, dans l'ordre de création."""
    async with Session() as session:
        r = await session.execute(select(Consigne).order_by(Consigne.id))
        return [c for c in r.scalars() if c.est_active()]


async def enregistrer_demande_db(
    identifiant: str, nom_client: str, details: str, date_souhaitee: str = ""
) -> int:
    async with Session() as session:
        d = Demande(
            identifiant=identifiant,
            nom_client=nom_client,
            details=details,
            date_souhaitee=date_souhaitee,
        )
        session.add(d)
        await session.commit()
        await session.refresh(d)
        return d.id


async def initialiser_base() -> None:
    async with moteur.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await migrer_colonnes()
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


async def enregistrer_message(
    telephone: str, role: str, contenu: str, auteur: str = "", valide_par: str = ""
) -> None:
    if not auteur:
        auteur = "client" if role == "user" else "agent"
    async with Session() as session:
        session.add(
            Message(
                telephone=telephone,
                role=role,
                contenu=contenu,
                auteur=auteur,
                valide_par=valide_par,
            )
        )
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
