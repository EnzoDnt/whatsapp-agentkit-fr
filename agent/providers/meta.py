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

# WhatsApp refuse tout corps de message au-delà de 4096 caractères. Un modèle
# bavard, ou un prompt système qui l'invite à détailler, dépasse ce seuil sans
# prévenir : Meta rejette alors l'envoi et le client ne reçoit rien du tout.
# Mieux vaut une réponse tronquée proprement qu'un silence.
MAX_CARACTERES_WHATSAPP = 4096


# Types de média que Meta livre avec un objet {id, mime_type, url, …}.
# "sticker" est reconnu pour pouvoir l'escalader proprement plutôt que de le
# laisser disparaître, mais il n'est pas traité.
TYPES_MEDIA = ("audio", "image", "video", "document", "sticker")


def _extraire_media(msg: dict, type_msg: str) -> dict | None:
    """
    Normalise l'objet média d'un message Meta.

    Retourne None si le type est inconnu ou l'objet inexploitable : l'appelant
    ignore alors le message plutôt que de fabriquer un média vide.

    Note : depuis 2026 Meta fournit « url » directement dans le webhook, en plus
    de « id ». Cette URL expire en 5 minutes — on la transporte pour gagner un
    aller-retour quand elle est encore fraîche, mais « media_id » reste la seule
    référence durable (voir medias.py).
    """
    if type_msg not in TYPES_MEDIA:
        return None

    objet = msg.get(type_msg) or {}
    identifiant = (objet.get("id") or "").strip()
    if not identifiant:
        return None

    # Un sticker est une image, mais sans intérêt pour un service client :
    # on le marque « autre » pour qu'il parte en escalade sans traitement.
    type_media = "autre" if type_msg == "sticker" else type_msg

    return {
        "type_media": type_media,
        "media_id": identifiant,
        "mime_type": (objet.get("mime_type") or "").strip(),
        "media_url": (objet.get("url") or "").strip(),
        "legende": (objet.get("caption") or "").strip(),
        "nom_fichier": (objet.get("filename") or "").strip(),
        "est_vocal": bool(objet.get("voice")),
    }


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

                # Le profil vit dans contacts[], pas dans messages[].
                profils: dict[str, dict] = {}
                for contact in valeur.get("contacts", []):
                    profil = contact.get("profile") or {}
                    fiche = {
                        "username": profil.get("username", ""),
                        "nom": profil.get("name", ""),
                        "pays": profil.get("country_code", ""),
                    }
                    for cle in (contact.get("wa_id"), contact.get("user_id")):
                        if cle:
                            profils[cle] = fiche

                for msg in valeur.get("messages", []):
                    type_msg = msg.get("type") or ""

                    # Un média n'est plus ignoré : on transporte de quoi le
                    # récupérer, et medias.py le convertira en texte. Auparavant
                    # tout ce qui n'était pas du texte tombait ici en silence —
                    # le client envoyait une note vocale et n'avait aucune réponse.
                    media = {}
                    if type_msg != "text":
                        media = _extraire_media(msg, type_msg)
                        if media is None:
                            logger.info(f"Message de type '{type_msg}' sans contenu exploitable : ignoré")
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
                            username=profils.get(identifiant, {}).get("username", ""),
                            type_media=media.get("type_media", ""),
                            media_id=media.get("media_id", ""),
                            mime_type=media.get("mime_type", ""),
                            media_url=media.get("media_url", ""),
                            legende=media.get("legende", ""),
                            nom_fichier=media.get("nom_fichier", ""),
                            est_vocal=media.get("est_vocal", False),
                            contexte={
                                "evenement_id": msg.get("id", ""),
                                "bsuid": bsuid,
                                "telephone": telephone,
                                "nom_profil": profils.get(identifiant, {}).get("nom", ""),
                                "pays": profils.get(identifiant, {}).get("pays", ""),
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
        if len(message) > MAX_CARACTERES_WHATSAPP:
            logger.warning(
                f"Réponse de {len(message)} caractères tronquée à {MAX_CARACTERES_WHATSAPP} : "
                "WhatsApp aurait refusé l'envoi. Demandez des réponses plus courtes "
                "dans le prompt système."
            )
            message = message[: MAX_CARACTERES_WHATSAPP - 1].rstrip() + "…"

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
