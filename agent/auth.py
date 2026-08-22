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

import asyncio
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
from sqlalchemy import Boolean, DateTime, Integer, String, func, select
from sqlalchemy.orm import Mapped, mapped_column

from agent.memory import Base, Session

logger = logging.getLogger("agentkit")

NOM_COOKIE = "agentkit_session"
DUREE_SESSION_S = int(os.getenv("DUREE_SESSION_HEURES", "12") or "12") * 3600
from agent.environnement import est_production  # noqa: E402

# Paramètres scrypt. Le coût mémoire vaut 128 × N × r, soit 32 Mo ici : assez
# pour rendre une attaque par dictionnaire coûteuse, sans dépasser ce qu'un
# petit serveur encaisse à chaque connexion.
# `maxmem` doit être passé explicitement : OpenSSL plafonne à 32 Mo par défaut
# et refuse le calcul sans cette autorisation.
_N, _R, _P = 2**15, 8, 1
_MAXMEM = 128 * _N * _R * 2


# Secret de repli, tiré au sort une seule fois par processus.
#
# Il a remplacé une constante écrite en dur dans ce fichier. Cette constante
# était publiée avec le dépôt : sur une installation où ENVIRONMENT n'avait pas
# été renseigné, n'importe qui pouvait fabriquer un cookie de session valide et
# lire toutes les conversations clients, sans mot de passe. Le scénario n'avait
# rien de théorique — ENVIRONMENT n'est pas une clé d'API, rien ne bloque au
# démarrage quand il manque, et il s'oublie donc silencieusement.
#
# Un secret aléatoire fait que le pire cas devient « les sessions tombent au
# redémarrage », au lieu de « tout le monde est administrateur ».
_SECRET_DE_REPLI = secrets.token_hex(32)
_repli_signale = False


def _secret_session() -> bytes:
    """
    Clé de signature des sessions.

    En production, SESSION_SECRET est obligatoire : une clé tirée au démarrage
    serait différente sur chaque instance et déconnecterait tout le monde à
    chaque redéploiement.
    """
    global _repli_signale

    valeur = os.getenv("SESSION_SECRET", "").strip()
    if valeur:
        return valeur.encode("utf-8")
    if est_production():
        raise RuntimeError(
            "SESSION_SECRET est obligatoire en production. "
            "Générez-le : openssl rand -hex 32"
        )
    if not _repli_signale:
        _repli_signale = True
        logger.warning(
            "SESSION_SECRET absent : clé de session tirée au hasard pour ce "
            "démarrage. Les sessions ouvertes tomberont au prochain redémarrage. "
            "En ligne, définissez SESSION_SECRET (openssl rand -hex 32)."
        )
    return _SECRET_DE_REPLI.encode("utf-8")


class Utilisateur(Base):
    __tablename__ = "utilisateurs"
    # La table est déclarée ici mais rattachée au Base de memory.py. Sans
    # `extend_existing`, un rechargement de ce seul module (rechargement à
    # chaud, test, import tardif) lève « Table already defined » alors que rien
    # n'a changé.
    __table_args__ = {"extend_existing": True}

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


def exiger_secret() -> None:
    """
    Vérifie que la console peut signer des sessions, et l'explique sinon.

    Sans ce garde-fou, l'absence de SESSION_SECRET en production remonte en
    « 500 Internal Server Error » sur chaque requête : techniquement correct,
    illisible pour la personne qui vient de déployer. Ici elle lit la cause et
    la commande à lancer.
    """
    try:
        _secret_session()
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e


def lire_jeton(jeton: str) -> int | None:
    """Retourne l'identifiant de l'utilisateur, ou None si le jeton est invalide."""
    try:
        ident, expire, signature = jeton.rsplit(".", 2)
        charge = f"{ident}.{expire}"
    except ValueError:
        return None

    try:
        secret = _secret_session()
    except RuntimeError:
        # Configuration incomplète : aucune session ne peut être authentifiée.
        # On refuse plutôt que de laisser passer.
        return None

    attendue = hmac.new(secret, charge.encode("utf-8"), hashlib.sha256).hexdigest()
    try:
        if not hmac.compare_digest(attendue, signature):
            return None
    except TypeError:
        return None

    if int(expire) < time.time():
        return None
    return int(ident)


def poser_cookie(reponse: Response, jeton: str, request: Request | None = None) -> None:
    """
    Dépose le cookie de session.

    `secure` ne se déduit plus du seul ENVIRONMENT : on regarde d'abord si la
    requête est arrivée en HTTPS. Une console servie en HTTPS derrière Coolify
    ou Railway obtient donc un cookie protégé même si ENVIRONMENT a été oublié,
    au lieu de laisser le jeton circuler en clair.
    """
    reponse.set_cookie(
        NOM_COOKIE,
        jeton,
        max_age=DUREE_SESSION_S,
        httponly=True,   # inaccessible au JavaScript : bloque le vol par XSS
        samesite="lax",  # bloque l'envoi depuis un site tiers (CSRF)
        secure=est_production() or _requete_https(request),
        path="/",
    )


def _requete_https(request: Request | None) -> bool:
    """
    La requête est-elle arrivée en HTTPS ?

    Derrière un reverse proxy (le cas de Coolify, Railway, Traefik, nginx), le
    serveur voit du HTTP en clair : c'est le proxy qui a terminé le TLS. Le
    schéma d'origine ne subsiste que dans X-Forwarded-Proto.
    """
    if request is None:
        return False
    if request.url.scheme == "https":
        return True
    transmis = request.headers.get("x-forwarded-proto", "")
    return transmis.split(",")[0].strip().lower() == "https"


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
        if essais:
            self._tentatives[ip] = essais
        else:
            # Ne pas laisser une entrée vide derrière soi : le dictionnaire est
            # indexé par adresse IP, et une attaque qui les fait tourner le
            # ferait grossir sans fin.
            self._tentatives.pop(ip, None)
        self._purger(maintenant)
        return len(essais) < self.maximum

    def _purger(self, maintenant: float) -> None:
        """Oublie les adresses dont la fenêtre est écoulée."""
        if len(self._tentatives) < 1000:
            return
        for ip in [
            i for i, ts in list(self._tentatives.items())
            if not ts or maintenant - ts[-1] > self.fenetre_s
        ]:
            self._tentatives.pop(ip, None)

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

    empreinte, sel = await asyncio.to_thread(hacher, mot_de_passe)
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


async def _hacher_hors_boucle(mot_de_passe: str, sel: str) -> str:
    """
    Calcule l'empreinte dans un fil séparé.

    scrypt coûte ~50 ms de calcul pur. Exécuté dans la boucle événementielle, il
    la fige : dix tentatives de connexion simultanées la bloquent 500 ms, et une
    poignée d'adresses IP suffit à dépasser les 5 secondes au-delà desquelles
    Meta considère le webhook en échec et rejoue le message. Une attaque par
    force brute sur la console dégradait donc le service WhatsApp lui-même.
    """
    empreinte, _ = await asyncio.to_thread(hacher, mot_de_passe, sel)
    return empreinte


async def authentifier(email: str, mot_de_passe: str) -> Utilisateur | None:
    async with Session() as session:
        r = await session.execute(
            select(Utilisateur).where(Utilisateur.email == email.strip().lower())
        )
        u = r.scalar_one_or_none()

    # On calcule un hachage même quand le compte n'existe pas, et même quand il
    # est désactivé : sinon le temps de réponse révèle quelles adresses sont
    # enregistrées, et lesquelles ont encore un accès actif.
    if u is None:
        await _hacher_hors_boucle(mot_de_passe, "sel-factice-temps-constant")
        return None

    calcule = await _hacher_hors_boucle(mot_de_passe, u.sel)
    if not hmac.compare_digest(calcule, u.empreinte):
        return None
    if u.actif is False:
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


async def changer_mot_de_passe(identifiant: int, ancien: str, nouveau: str) -> None:
    """
    Change le mot de passe d'un compte, après vérification de l'ancien.

    Exiger l'ancien n'est pas une formalité : sans lui, un poste laissé
    déverrouillé suffit à confisquer le compte de son propriétaire.
    """
    if len(nouveau) < 10:
        raise HTTPException(400, "Le nouveau mot de passe doit faire au moins 10 caractères")

    async with Session() as session:
        u = await session.get(Utilisateur, identifiant)
        if u is None:
            raise HTTPException(404, "Compte introuvable")
        calcule = await _hacher_hors_boucle(ancien, u.sel)
        if not hmac.compare_digest(calcule, u.empreinte):
            raise HTTPException(403, "Mot de passe actuel incorrect")

        empreinte, sel = await asyncio.to_thread(hacher, nouveau)
        u.empreinte, u.sel = empreinte, sel
        await session.commit()
    logger.info(f"Mot de passe changé : {u.email}")


async def definir_activation(identifiant: int, actif: bool, demandeur_id: int) -> str:
    """
    Active ou désactive un compte.

    On refuse de se désactiver soi-même : c'est le moyen le plus simple de
    fermer la console à tout le monde par erreur, sans possibilité de revenir
    en arrière depuis l'interface.
    """
    if identifiant == demandeur_id and not actif:
        raise HTTPException(400, "Vous ne pouvez pas désactiver votre propre compte")

    async with Session() as session:
        u = await session.get(Utilisateur, identifiant)
        if u is None:
            raise HTTPException(404, "Compte introuvable")
        if not actif:
            restants = await session.execute(
                select(func.count(Utilisateur.id)).where(
                    Utilisateur.actif.is_(True), Utilisateur.id != identifiant
                )
            )
            if (restants.scalar() or 0) == 0:
                raise HTTPException(400, "C'est le dernier compte actif : il ne peut pas être désactivé")
        u.actif = actif
        courriel = u.email
        await session.commit()

    logger.info(f"Compte {'réactivé' if actif else 'désactivé'} : {courriel}")
    return courriel
