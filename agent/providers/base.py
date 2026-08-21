"""Interface commune à tous les fournisseurs WhatsApp."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from fastapi import Request


@dataclass
class MessageEntrant:
    """Message normalisé : format identique quel que soit le fournisseur."""

    telephone: str          # E.164 sans le "+"
    texte: str
    message_id: str
    est_sortant: bool       # True si c'est l'agent qui l'a envoyé — on l'ignore
    contexte: dict = field(default_factory=dict)


class ErreurConfiguration(RuntimeError):
    """Configuration invalide : on refuse de démarrer plutôt que de mal tourner."""


class FournisseurWhatsApp(ABC):
    """Contrat que chaque fournisseur implémente."""

    nom = "base"

    @abstractmethod
    async def parser_webhook(self, request: Request) -> list[MessageEntrant]: ...

    @abstractmethod
    async def envoyer_message(
        self, telephone: str, message: str, contexte: dict | None = None
    ) -> bool: ...

    @abstractmethod
    async def verifier_signature(self, request: Request) -> bool: ...

    async def valider_webhook(self, request: Request) -> str | None:
        """Vérification GET du webhook. Seul Meta l'utilise."""
        return None

    async def verifier_connexion(self) -> tuple[bool, str]:
        return True, "Ce fournisseur n'expose pas de vérification de connexion"


def mode_developpement() -> bool:
    return os.getenv("ENVIRONMENT", "development").strip().lower() != "production"


def autoriser_webhook_non_signe() -> bool:
    """
    Le kit d'origine laissait passer TOUT webhook quand le secret était vide.
    N'importe qui connaissant l'URL pouvait injecter des messages, brûler des
    crédits Claude et faire parler le numéro professionnel du client.

    Ici il faut DEUX conditions explicites, et jamais en production :
      ENVIRONMENT != production  ET  AUTORISER_WEBHOOK_NON_SIGNE=true
    """
    if not mode_developpement():
        return False
    return os.getenv("AUTORISER_WEBHOOK_NON_SIGNE", "false").strip().lower() == "true"
