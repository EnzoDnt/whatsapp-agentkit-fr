"""Interface commune à tous les fournisseurs WhatsApp."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from fastapi import Request

from agent.environnement import est_production


@dataclass
class MessageEntrant:
    """
    Message normalisé : format identique quel que soit le fournisseur.

    `identifiant` est la clé de conversation. Ce n'est PAS toujours un numéro :
    depuis juillet 2026, un client ayant adopté un username WhatsApp peut écrire
    sans partager son numéro. Meta livre alors un BSUID (business-scoped user ID)
    et laisse `from` vide. Tout le code doit donc parler d'identifiant, pas de
    téléphone — sinon ces clients sont purement ignorés.
    """

    identifiant: str        # numéro E.164 sans "+", OU BSUID
    texte: str
    message_id: str
    est_sortant: bool       # True si c'est l'agent qui l'a envoyé — on l'ignore
    par_bsuid: bool = False  # True si l'identifiant est un BSUID et non un numéro
    username: str = ""       # @pseudo, si le client en a adopté un
    contexte: dict = field(default_factory=dict)

    # ── Média joint ──────────────────────────────────────────────────────
    # "texte" reste la charge utile que verra le modèle : pour un média, il est
    # rempli plus tard par la transcription ou la description (voir medias.py).
    type_media: str = ""     # "" | audio | image | video | document | autre
    media_id: str = ""       # identifiant Meta — la SEULE référence à conserver
    mime_type: str = ""
    media_url: str = ""      # URL fournie par le webhook : périssable, 5 minutes
    legende: str = ""        # caption : porte souvent l'intention réelle
    nom_fichier: str = ""    # documents uniquement
    est_vocal: bool = False  # note vocale (micro) vs fichier audio joint
    media_cle: str = ""      # clé du fichier une fois conservé, pour la console

    @property
    def a_un_media(self) -> bool:
        return bool(self.type_media)

    @property
    def telephone(self) -> str:
        """Compatibilité : ancien nom du champ."""
        return self.identifiant


class ErreurConfiguration(RuntimeError):
    """Configuration invalide : on refuse de démarrer plutôt que de mal tourner."""


class FournisseurWhatsApp(ABC):
    """Contrat que chaque fournisseur implémente."""

    nom = "base"

    @abstractmethod
    async def parser_webhook(self, request: Request) -> list[MessageEntrant]: ...

    @abstractmethod
    async def envoyer_message(
        self, destinataire: str, message: str, contexte: dict | None = None
    ) -> bool: ...

    @abstractmethod
    async def verifier_signature(self, request: Request) -> bool: ...

    async def valider_webhook(self, request: Request) -> str | None:
        """Vérification GET du webhook. Seul Meta l'utilise."""
        return None

    async def verifier_connexion(self) -> tuple[bool, str]:
        return True, "Ce fournisseur n'expose pas de vérification de connexion"


def mode_developpement() -> bool:
    """
    Tourne-t-on sur un poste de développement ?

    S'appuie sur la détection d'hébergeur, et non sur la seule déclaration de
    l'utilisateur : sinon un oubli d'ENVIRONMENT sur Coolify ou Railway suffisait
    à rendre effective la dispense de signature ci-dessous, en pleine production.
    """
    return not est_production()


def autoriser_webhook_non_signe() -> bool:
    """
    Le kit d'origine laissait passer TOUT webhook quand le secret était vide.
    N'importe qui connaissant l'URL pouvait injecter des messages, brûler des
    crédits Claude et faire parler le numéro professionnel du client.

    Ici il faut DEUX conditions explicites, et jamais en production :
      aucun hébergeur détecté  ET  AUTORISER_WEBHOOK_NON_SIGNE=true
    """
    if not mode_developpement():
        return False
    return os.getenv("AUTORISER_WEBHOOK_NON_SIGNE", "false").strip().lower() == "true"
