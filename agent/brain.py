"""
Cerveau de l'agent : dialogue avec Claude, et exécution réelle des outils.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

import yaml
from anthropic import AsyncAnthropic
from dotenv import load_dotenv

from agent.securite import depenses, masquer_telephone
from agent.tools import executer_outil, outil_accepte, schemas_outils

load_dotenv()
logger = logging.getLogger("agentkit")

client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# Modèle réglable depuis .env, sans toucher au code.
#   claude-opus-5     le plus capable          5 $ / 25 $ par million de tokens
#   claude-sonnet-5   l'équilibré (défaut)     3 $ / 15 $
#   claude-haiku-4-5  le plus rapide           1 $ / 5 $
# Le « or » plutôt que le défaut de getenv : une variable déclarée mais vide
# renvoie "" et laisserait l'agent sans modèle.
MODELE = os.getenv("ANTHROPIC_MODEL") or "claude-sonnet-5"

# Un bot de réponses courtes : un effort faible répond plus vite et moins cher.
EFFORT = os.getenv("ANTHROPIC_EFFORT", "low").strip()

# Attention : ce plafond ne concerne pas que la réponse visible. Sur les modèles
# actuels le raisonnement interne compte aussi dedans.
MAX_TOKENS = int(os.getenv("ANTHROPIC_MAX_TOKENS") or "4096")

# Nombre maximum d'allers-retours d'outils pour un seul message client.
# Garde-fou contre une boucle où le modèle rappellerait un outil sans fin.
MAX_TOURS_OUTILS = int(os.getenv("MAX_TOURS_OUTILS", "5") or "5")

# Certains modèles anciens refusent output_config. On retient l'échec.
_supporte_effort = True


def charger_config_prompts() -> dict:
    try:
        with open("config/prompts.yaml", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.error("config/prompts.yaml introuvable")
        return {}


def prompt_systeme() -> str:
    return charger_config_prompts().get(
        "system_prompt", "Tu es un assistant utile. Réponds toujours en français."
    )


JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre")


def horodatage() -> str:
    """
    La date du jour, en clair, à ajouter au prompt système.

    Sans elle, le modèle ignore la date : il ne peut pas interpréter « demain »,
    « samedi prochain » ou « dans deux semaines », et il invente une date. Pour
    un agent qui prend des commandes datées, c'est rédhibitoire.

    Placé en FIN de prompt système à dessein : c'est la seule partie qui change
    à chaque appel. Si vous activez un jour le cache de prompt, tout ce qui
    précède reste ainsi réutilisable.
    """
    m = datetime.now()
    return (
        f"\n\n## Date et heure actuelles\n"
        f"Nous sommes le {JOURS[m.weekday()]} {m.day} {MOIS[m.month - 1]} {m.year}, "
        f"il est {m:%H}h{m:%M}.\n"
        f"Format à passer aux outils qui attendent une date : AAAA-MM-JJ HH:MM "
        f"(aujourd'hui = {m:%Y-%m-%d}).\n"
        f"Calcule toujours les dates à partir de celle-ci. Ne demande jamais au "
        f"client quel jour on est."
    )


def message_erreur() -> str:
    return charger_config_prompts().get(
        "error_message",
        "Désolé, je rencontre un problème technique. Réessayez dans quelques minutes.",
    )


def message_incompris() -> str:
    return charger_config_prompts().get(
        "fallback_message", "Désolé, je n'ai pas bien compris. Pouvez-vous reformuler ?"
    )


def message_saturation() -> str:
    return charger_config_prompts().get(
        "quota_message",
        "Nous avons atteint notre limite de messages automatiques pour aujourd'hui. "
        "Un membre de l'équipe vous répondra dès que possible.",
    )


def _extraire_texte(reponse) -> str:
    """
    Concatène les blocs de texte de la réponse.

    On ne peut PAS faire reponse.content[0].text : le contenu est une liste de
    blocs et le premier n'est pas toujours du texte (les modèles qui raisonnent
    renvoient d'abord un bloc de réflexion). Il faut filtrer par type.
    """
    parts = [b.text for b in reponse.content if getattr(b, "type", None) == "text"]
    return "\n".join(p for p in parts if p).strip()


def _erreur_due_a_effort(e: Exception) -> bool:
    """Vrai seulement si l'appel a été refusé À CAUSE de output_config/effort."""
    if getattr(e, "status_code", None) != 400:
        return False
    texte = str(e).lower()
    return "output_config" in texte or "effort" in texte


async def generer_reponse(
    message: str, historique: list[dict], telephone: str = ""
) -> tuple[str, bool]:
    """
    Produit la réponse de l'agent.

    Retourne (texte, est_une_vraie_reponse). Le booléen vaut False pour les avis
    techniques (erreur, incompréhension, quota) : main.py s'en sert pour ne PAS
    les enregistrer dans l'historique, sinon ils pollueraient le contexte de tous
    les messages suivants.
    """
    global _supporte_effort

    if not message or len(message.strip()) < 2:
        return message_incompris(), False

    if depenses.depassement():
        logger.error(
            f"Plafond de dépense journalier atteint ({depenses.depense_du_jour} $) : "
            "appel à Claude refusé."
        )
        return message_saturation(), False

    messages: list[dict] = [{"role": m["role"], "content": m["content"]} for m in historique]
    messages.append({"role": "user", "content": message})

    systeme = prompt_systeme() + horodatage()
    outils = schemas_outils()

    async def appeler(extra: dict):
        return await client.messages.create(
            model=MODELE,
            max_tokens=MAX_TOKENS,
            system=systeme,
            messages=messages,
            tools=outils,
            **extra,
        )

    for tour in range(MAX_TOURS_OUTILS):
        extra = (
            {"output_config": {"effort": EFFORT}} if (_supporte_effort and EFFORT) else {}
        )
        try:
            reponse = await appeler(extra)
        except Exception as e:  # noqa: BLE001
            if extra and _erreur_due_a_effort(e):
                logger.warning(
                    f"{MODELE} refuse output_config.effort ; nouvel essai sans ce paramètre."
                )
                _supporte_effort = False
                try:
                    reponse = await appeler({})
                except Exception as e2:  # noqa: BLE001
                    logger.error(f"Échec de l'appel à Claude : {e2}")
                    return message_erreur(), False
            else:
                logger.error(f"Échec de l'appel à Claude : {e}")
                return message_erreur(), False

        cout = depenses.enregistrer(
            MODELE, reponse.usage.input_tokens, reponse.usage.output_tokens
        )
        logger.info(
            f"Claude {MODELE} — {reponse.usage.input_tokens} entrée / "
            f"{reponse.usage.output_tokens} sortie (~{cout:.4f} $, "
            f"cumul du jour {depenses.depense_du_jour} $)"
        )

        if reponse.stop_reason != "tool_use":
            if reponse.stop_reason == "max_tokens":
                logger.warning(
                    f"Réponse tronquée au plafond de {MAX_TOKENS} tokens. "
                    "Augmentez ANTHROPIC_MAX_TOKENS ou raccourcissez le prompt système."
                )
            texte = _extraire_texte(reponse)
            if not texte:
                logger.warning("Claude a renvoyé une réponse sans texte")
                return message_incompris(), False
            return texte, True

        # ── Le modèle demande un ou plusieurs outils ──────────────────────
        # On renvoie TOUS les tool_result dans UN SEUL message utilisateur :
        # les répartir sur plusieurs messages apprend au modèle à ne plus
        # paralléliser ses appels.
        messages.append({"role": "assistant", "content": reponse.content})
        resultats = []
        for bloc in reponse.content:
            if getattr(bloc, "type", None) != "tool_use":
                continue
            arguments = dict(bloc.input or {})

            # Le numéro vient du webhook, jamais du modèle : sinon une injection
            # de prompt pourrait lui faire enregistrer une demande au nom d'un
            # autre client. Mais on ne l'injecte QUE dans les outils qui le
            # déclarent — l'ajouter aux autres lève un TypeError et fait perdre
            # sa réponse au client.
            if telephone and outil_accepte(bloc.name, "telephone"):
                arguments["telephone"] = telephone

            logger.info(f"Outil appelé : {bloc.name}")
            resultats.append(
                {
                    "type": "tool_result",
                    "tool_use_id": bloc.id,
                    "content": executer_outil(bloc.name, arguments),
                }
            )
        messages.append({"role": "user", "content": resultats})

    logger.warning(
        f"{MAX_TOURS_OUTILS} tours d'outils atteints pour "
        f"{masquer_telephone(telephone)} sans réponse finale."
    )
    return message_erreur(), False
