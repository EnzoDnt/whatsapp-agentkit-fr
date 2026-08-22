"""
Authentification de la console : comptes nommés et sessions signées.

Choix de conception : aucune dépendance supplémentaire. Le hachage utilise
`hashlib.scrypt` (bibliothèque standard, résistant au matériel dédié) et les
sessions sont des jetons signés en HMAC-SHA256. Ajouter bcrypt ou argon2 pour
ce cas d'usage n'apporterait rien qu'une dépendance de plus à installer chez
le client.

Pourquoi des comptes nommés plutôt qu'un mot de passe partagé : quand une
personne reprend la main sur une conversation client ou modifie le comportement
de l'agent, on doit savoir qui.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import HTTPException, Request, Response
from sqlalchemy import Boolean, DateTime, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column

from agent.memory import Base, Session

logger = logging.getLogger("agentkit")

NOM_COOKIE = "agentkit_session"
DUREE_SESSION_S = int(os.getenv("DUREE_SESSION_HEURES", "12") or "12") * 3600
EST_PRODUCTION = os.getenv("ENVIRONMENT", "development").strip().lower() == "production"

# Paramètres scrypt. Le coût mémoire vaut 128 × N × r, soit 32 Mo ici : assez
# pour rendre une attaque par dictionnaire coûteuse, sans dépasser ce qu'un
# petit serveur encaisse à chaque connexion.
# `maxmem` doit être passé explicitement : OpenSSL plafonne à 32 Mo par défaut
# et refuse le calcul sans cette autorisation.
_N, _R, _P = 2**15, 8, 1
_MAXMEM = 128 * _N * _R * 2


def _secret_session() -> bytes:
    """
    Clé de signature des sessions.

    En production, SESSION_SECRET est obligatoire : sans lui, un redémarrage
    invaliderait toutes les sessions, et pire, une clé générée à la volée serait
    différente sur chaque instance.
    """
    valeur = os.getenv("SESSION_SECRET", "").strip()
    if valeur:
        return valeur.encode("utf-8")
    if EST_PRODUCTION:
        raise RuntimeError(
            "SESSION_SECRET est obligatoire en production. "
            "Générez-le : openssl rand -hex 32"
        )
    return b"secret-de-developpement-non-securise"


class Utilisateur(Base):
    __tablename__ = "utilisateurs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    nom: Mapped[str] = mapped_column(String(120))
    empreinte: Mapped[str] = mapped_column(String(300))
    sel: Mapped[str] = mapped_column(String(64))
    actif: Mapped[bool] = mapped_column(Boolean, default=True)
    cree_le: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    dernier_acces: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


def hacher(mot_de_passe: str, sel: str | None = None) -> tuple[str, str]:
    sel = sel or secrets.token_hex(16)
    brut = hashlib.scrypt(
        mot_de_passe.encode("utf-8"),
        salt=sel.encode("utf-8"),
        n=_N, r=_R, p=_P, dklen=64, maxmem=_MAXMEM,
    )
    return base64.b64encode(brut).decode("ascii"), sel


def verifier_mot_de_passe(mot_de_passe: str, empreinte: str, sel: str) -> bool:
    calcule, _ = hacher(mot_de_passe, sel)
    return hmac.compare_digest(calcule, empreinte)


# ── Sessions ─────────────────────────────────────────────────────────────


def creer_jeton(utilisateur_id: int) -> str:
    """Jeton « id.expiration.signature » — sans état côté serveur."""
    expire = int(time.time()) + DUREE_SESSION_S
    charge = f"{utilisateur_id}.{expire}"
    signature = hmac.new(_secret_session(), charge.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{charge}.{signature}"


def lire_jeton(jeton: str) -> int | None:
    """Retourne l'identifiant de l'utilisateur, ou None si le jeton est invalide."""
    try:
        ident, expire, signature = jeton.rsplit(".", 2)
        charge = f"{ident}.{expire}"
    except ValueError:
        return None

    attendue = hmac.new(_secret_session(), charge.encode("utf-8"), hashlib.sha256).hexdigest()
    try:
        if not hmac.compare_digest(attendue, signature):
            return None
    except TypeError:
        return None

    if int(expire) < time.time():
        return None
    return int(ident)


def poser_cookie(reponse: Response, jeton: str) -> None:
    reponse.set_cookie(
        NOM_COOKIE,
        jeton,
        max_age=DUREE_SESSION_S,
        httponly=True,          # inaccessible au JavaScript : bloque le vol par XSS
        samesite="lax",         # bloque l'envoi depuis un site tiers (CSRF)
        secure=EST_PRODUCTION,  # HTTPS obligatoire en production
        path="/",
    )


def retirer_cookie(reponse: Response) -> None:
    reponse.delete_cookie(NOM_COOKIE, path="/")


# ── Limitation des tentatives ────────────────────────────────────────────


@dataclass
class LimiteurConnexion:
    """
    Freine les tentatives de connexion par adresse IP.

    Sans ça, un mot de passe faible tombe en quelques heures de force brute.
    """

    maximum: int = 8
    fenetre_s: int = 900
    _tentatives: dict[str, list[float]] = field(default_factory=dict)

    def autorise(self, ip: str) -> bool:
        maintenant = time.monotonic()
        essais = [t for t in self._tentatives.get(ip, []) if maintenant - t < self.fenetre_s]
        self._tentatives[ip] = essais
        return len(essais) < self.maximum

    def echec(self, ip: str) -> None:
        self._tentatives.setdefault(ip, []).append(time.monotonic())

    def succes(self, ip: str) -> None:
        self._tentatives.pop(ip, None)


limiteur_connexion = LimiteurConnexion()


# ── Opérations ───────────────────────────────────────────────────────────


async def aucun_utilisateur() -> bool:
    async with Session() as session:
        r = await session.execute(select(Utilisateur).limit(1))
        return r.scalar_one_or_none() is None


async def creer_utilisateur(email: str, nom: str, mot_de_passe: str) -> int:
    email = email.strip().lower()
    if len(mot_de_passe) < 10:
        raise HTTPException(400, "Le mot de passe doit faire au moins 10 caractères")
    if "@" not in email:
        raise HTTPException(400, "Adresse e-mail invalide")

    empreinte, sel = hacher(mot_de_passe)
    async with Session() as session:
        existant = await session.execute(select(Utilisateur).where(Utilisateur.email == email))
        if existant.scalar_one_or_none():
            raise HTTPException(409, "Un compte existe déjà avec cette adresse")
        u = Utilisateur(email=email, nom=nom.strip() or email, empreinte=empreinte, sel=sel)
        session.add(u)
        await session.commit()
        await session.refresh(u)
        logger.info(f"Compte créé : {email}")
        return u.id


async def authentifier(email: str, mot_de_passe: str) -> Utilisateur | None:
    async with Session() as session:
        r = await session.execute(
            select(Utilisateur).where(Utilisateur.email == email.strip().lower())
        )
        u = r.scalar_one_or_none()

    # On calcule un hachage même quand le compte n'existe pas : sinon le temps
    # de réponse révèle quelles adresses sont enregistrées.
    if u is None:
        hacher(mot_de_passe, "sel-factice-temps-constant")
        return None
    if u.actif is False or not verifier_mot_de_passe(mot_de_passe, u.empreinte, u.sel):
        return None

    async with Session() as session:
        frais = await session.get(Utilisateur, u.id)
        frais.dernier_acces = datetime.now(timezone.utc)
        await session.commit()
    return u


async def utilisateur_courant(request: Request) -> Utilisateur:
    """Dépendance FastAPI : refuse la requête si la session est absente ou expirée."""
    jeton = request.cookies.get(NOM_COOKIE, "")
    identifiant = lire_jeton(jeton) if jeton else None
    if identifiant is None:
        raise HTTPException(401, "Session expirée ou absente")

    async with Session() as session:
        u = await session.get(Utilisateur, identifiant)
    if u is None or u.actif is False:
        raise HTTPException(401, "Compte introuvable ou désactivé")
    return u
