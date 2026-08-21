"""
Fournisseur « simulateur » : WhatsApp en local, sans aucun compte Meta.

Objectif : voir son agent vivre en 5 minutes, avec pour seul prérequis une clé
API Anthropic. Pas de compte développeur, pas de numéro, pas de tunnel.

Point clé de conception : cette classe HÉRITE de FournisseurMeta et ne remplace
que l'envoi. Le webhook, le format du payload et la vérification de signature
sont donc EXACTEMENT le code de production. Ce que vous validez en local est ce
qui tournera chez le client — un simulateur qui court-circuiterait le webhook ne
prouverait rien.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
import uuid

from agent.providers.meta import FournisseurMeta
from agent.securite import masquer_telephone

logger = logging.getLogger("agentkit")

# Secret propre au simulateur : les webhooks locaux sont signés pour de vrai.
SECRET_SIMULATEUR = os.getenv("SIMULATEUR_SECRET") or "secret-local-simulateur"

# Numéro fictif du « client » qui écrit depuis l'interface.
TELEPHONE_SIMULE = os.getenv("SIMULATEUR_TELEPHONE") or "33600000000"


class FileMessages:
    """Boîte de réception de l'interface : ce que l'agent a répondu."""

    def __init__(self) -> None:
        self._messages: list[dict] = []
        self._verrou = asyncio.Lock()

    async def ajouter(self, texte: str) -> None:
        async with self._verrou:
            self._messages.append(
                {"id": str(uuid.uuid4()), "texte": texte, "horodatage": time.time()}
            )

    async def depuis(self, index: int) -> tuple[list[dict], int]:
        """Retourne les messages postérieurs à `index`, et le nouvel index."""
        async with self._verrou:
            return self._messages[index:], len(self._messages)

    async def vider(self) -> None:
        async with self._verrou:
            self._messages.clear()


file_sortante = FileMessages()


class FournisseurSimulateur(FournisseurMeta):
    """Identique à Meta, sauf que l'envoi va vers l'interface locale."""

    nom = "simulateur"

    def __init__(self) -> None:
        # On force un secret présent : le simulateur signe réellement ses webhooks,
        # donc on n'emprunte jamais le chemin « non signé ».
        os.environ.setdefault("META_APP_SECRET", SECRET_SIMULATEUR)
        os.environ.setdefault("META_ACCESS_TOKEN", "simulateur")
        os.environ.setdefault("META_PHONE_NUMBER_ID", "simulateur")
        super().__init__()

    async def envoyer_message(
        self, destinataire: str, message: str, contexte: dict | None = None
    ) -> bool:
        await file_sortante.ajouter(message)
        logger.info(f"[simulateur] réponse remise à {masquer_telephone(destinataire)}")
        return True

    async def verifier_connexion(self) -> tuple[bool, str]:
        return True, "Simulateur local — aucun compte WhatsApp requis"


# ── Fabrication d'un webhook Meta authentique ────────────────────────────


def construire_payload(texte: str, telephone: str = TELEPHONE_SIMULE) -> dict:
    """
    Reproduit à l'identique la structure que Meta envoie réellement.

    Si ce format dérive un jour, le simulateur cassera en même temps que la
    production — ce qui est exactement le comportement souhaité.
    """
    message_id = f"wamid.SIM{uuid.uuid4().hex[:22].upper()}"
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "0",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "33600000000",
                                "phone_number_id": "simulateur",
                            },
                            "contacts": [
                                {
                                    "profile": {"name": "Client de test"},
                                    "wa_id": telephone,
                                }
                            ],
                            "messages": [
                                {
                                    "from": telephone,
                                    "id": message_id,
                                    "timestamp": str(int(time.time())),
                                    "type": "text",
                                    "text": {"body": texte},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def signer(corps: bytes) -> str:
    """Même algorithme que Meta : HMAC-SHA256 du corps brut, préfixé sha256=."""
    empreinte = hmac.new(SECRET_SIMULATEUR.encode("utf-8"), corps, hashlib.sha256).hexdigest()
    return f"sha256={empreinte}"


def payload_signe(texte: str, telephone: str = TELEPHONE_SIMULE) -> tuple[bytes, dict]:
    """Retourne (corps_brut, en-têtes) prêts à être postés sur /webhook."""
    corps = json.dumps(construire_payload(texte, telephone)).encode("utf-8")
    return corps, {"Content-Type": "application/json", "X-Hub-Signature-256": signer(corps)}
