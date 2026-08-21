"""
Adaptateur WhatsApp Cloud API (Meta), en direct.

C'est la voie recommandée en production : aucun intermédiaire ne voit les
conversations de vos clients, ce qui simplifie beaucoup la conformité RGPD
(pas de sous-traitant supplémentaire à encadrer par un DPA).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os

import httpx
from fastapi import Request

from agent.providers.base import (
    ErreurConfiguration,
    FournisseurWhatsApp,
    MessageEntrant,
    autoriser_webhook_non_signe,
    mode_developpement,
)
from agent.securite import masquer_telephone

logger = logging.getLogger("agentkit")


class FournisseurMeta(FournisseurWhatsApp):
    nom = "meta"

    def __init__(self) -> None:
        self.token = os.getenv("META_ACCESS_TOKEN", "").strip()
        self.phone_number_id = os.getenv("META_PHONE_NUMBER_ID", "").strip()
        self.verify_token = os.getenv("META_VERIFY_TOKEN") or "agentkit-verify"
        self.app_secret = os.getenv("META_APP_SECRET", "").strip()
        self.version_api = os.getenv("META_API_VERSION") or "v25.0"

        if not self.token or not self.phone_number_id:
            logger.warning(
                "META_ACCESS_TOKEN ou META_PHONE_NUMBER_ID manquant : "
                "l'agent ne pourra pas répondre."
            )

        # Refus de démarrer plutôt que d'accepter n'importe quel webhook.
        if not self.app_secret and not autoriser_webhook_non_signe():
            raise ErreurConfiguration(
                "META_APP_SECRET est vide : les webhooks ne peuvent pas être vérifiés.\n"
                "  → En production, renseignez META_APP_SECRET (obligatoire).\n"
                "  → En local seulement, vous pouvez mettre AUTORISER_WEBHOOK_NON_SIGNE=true\n"
                "    dans votre .env, en connaissance de cause."
            )
        if not self.app_secret:
            logger.warning(
                "⚠️  Webhooks NON vérifiés (AUTORISER_WEBHOOK_NON_SIGNE=true). "
                "Développement local uniquement — jamais avec de vrais clients."
            )

    # ── Réception ────────────────────────────────────────────────────────

    async def valider_webhook(self, request: Request) -> str | None:
        """Meta fait un GET avec hub.challenge pour vérifier que l'URL est bien la vôtre."""
        p = request.query_params
        if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == self.verify_token:
            return p.get("hub.challenge") or ""
        return None

    async def verifier_signature(self, request: Request) -> bool:
        if not self.app_secret:
            return autoriser_webhook_non_signe()

        entete = request.headers.get("X-Hub-Signature-256", "")
        if not entete.startswith("sha256="):
            logger.warning("Webhook sans en-tête X-Hub-Signature-256 : rejeté")
            return False

        corps = await request.body()
        attendue = hmac.new(self.app_secret.encode("utf-8"), corps, hashlib.sha256).hexdigest()

        # compare_digest sur des str exige de l'ASCII pur ; un en-tête exotique
        # lèverait TypeError, ce qui sortirait en 500 et ferait réessayer Meta 7 fois.
        try:
            valide = hmac.compare_digest(attendue, entete.removeprefix("sha256="))
        except TypeError:
            logger.warning("Signature de webhook non ASCII : rejetée")
            return False

        if not valide:
            logger.warning("Signature de webhook invalide : rejetée")
        return valide

    async def parser_webhook(self, request: Request) -> list[MessageEntrant]:
        """
        Lit le payload imbriqué de Meta.

        Depuis juillet 2026, un client ayant adopté un username WhatsApp peut
        écrire sans partager son numéro : Meta met alors `from` à la chaîne vide
        et fournit un BSUID dans `from_user_id`. Lire uniquement `from` — ce que
        font encore beaucoup d'intégrations — revient à ignorer purement et
        simplement ces clients.

        Meta ne renvoie le numéro que pendant 30 jours après le dernier échange
        avec ce numéro. Passé ce délai, un client existant réapparaît comme un
        inconnu si l'on ne s'appuie pas sur le BSUID. Le BSUID est donc
        l'identifiant de référence dès qu'il est présent et que le numéro manque.
        """
        corps = await request.json()
        messages: list[MessageEntrant] = []

        for entree in corps.get("entry", []):
            for changement in entree.get("changes", []):
                valeur = changement.get("value") or {}

                # Le username vit dans contacts[], pas dans messages[].
                usernames: dict[str, str] = {}
                for contact in valeur.get("contacts", []):
                    pseudo = (contact.get("profile") or {}).get("username", "")
                    for cle in (contact.get("wa_id"), contact.get("user_id")):
                        if cle and pseudo:
                            usernames[cle] = pseudo

                for msg in valeur.get("messages", []):
                    if msg.get("type") != "text":
                        # images, audio, documents : hors périmètre pour l'instant.
                        logger.info(f"Message de type '{msg.get('type')}' ignoré")
                        continue

                    telephone = (msg.get("from") or "").strip()
                    bsuid = (msg.get("from_user_id") or "").strip()
                    identifiant = telephone or bsuid

                    if not identifiant:
                        logger.warning(
                            "Message sans identifiant exploitable (ni from ni from_user_id) : ignoré"
                        )
                        continue

                    messages.append(
                        MessageEntrant(
                            identifiant=identifiant,
                            texte=(msg.get("text") or {}).get("body", ""),
                            message_id=msg.get("id", ""),
                            est_sortant=False,
                            par_bsuid=not telephone,
                            username=usernames.get(identifiant, ""),
                            contexte={
                                "evenement_id": msg.get("id", ""),
                                "bsuid": bsuid,
                                "telephone": telephone,
                            },
                        )
                    )
        return messages

    # ── Envoi ────────────────────────────────────────────────────────────

    async def envoyer_message(
        self, destinataire: str, message: str, contexte: dict | None = None
    ) -> bool:
        """
        Envoie un message texte.

        Sur POST /{phone_number_id}/messages, le champ `to` accepte aussi bien
        un numéro qu'un BSUID — inutile de distinguer les deux ici.
        (Le champ `recipient` séparé n'existe que sur /marketing_messages.)
        """
        telephone = destinataire
        if not self.token or not self.phone_number_id:
            logger.error("Envoi impossible : META_ACCESS_TOKEN ou META_PHONE_NUMBER_ID manquant")
            return False

        url = f"https://graph.facebook.com/{self.version_api}/{self.phone_number_id}/messages"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(
                    url,
                    json={
                        "messaging_product": "whatsapp",
                        "to": telephone,
                        "type": "text",
                        "text": {"body": message},
                    },
                    headers={"Authorization": f"Bearer {self.token}"},
                )
        except httpx.HTTPError as e:
            logger.error(f"Erreur réseau vers Meta : {e}")
            return False

        if r.status_code == 200:
            return True

        logger.error(
            f"Meta a refusé l'envoi à {masquer_telephone(telephone)} "
            f"[{r.status_code}] : {r.text[:300]}"
        )
        return False

    # ── Diagnostic ───────────────────────────────────────────────────────

    async def verifier_connexion(self) -> tuple[bool, str]:
        if not self.token or not self.phone_number_id:
            return False, "META_ACCESS_TOKEN ou META_PHONE_NUMBER_ID manquant"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(
                    f"https://graph.facebook.com/{self.version_api}/{self.phone_number_id}",
                    params={"fields": "display_phone_number,verified_name,quality_rating"},
                    headers={"Authorization": f"Bearer {self.token}"},
                )
        except httpx.HTTPError as e:
            return False, f"Meta injoignable : {e}"

        if r.status_code != 200:
            return False, f"Meta a répondu {r.status_code} : {r.text[:200]}"

        d = r.json()
        return True, (
            f"Numéro {d.get('display_phone_number', '?')} connecté "
            f"({d.get('verified_name', '?')}, qualité : {d.get('quality_rating', '?')})"
        )
