"""
Cerveau de l'agent : dialogue avec Claude, et exécution réelle des outils.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

import yaml
from dotenv import load_dotenv

from agent.llm import ErreurLLM, modele_par_defaut, obtenir_client
from agent.securite import depenses, masquer_telephone, sauvegarder_depense
from agent.tools import executer_outil, outil_accepte, schemas_outils

load_dotenv()
logger = logging.getLogger("agentkit")

# Fournisseur et modèle se règlent depuis .env, sans toucher au code.
#   anthropic  (défaut)  claude-opus-5 · claude-sonnet-5 · claude-haiku-4-5
#   openai               gpt-5.1 et suivants
#   openrouter           n'importe quel modèle du catalogue
#   google               gemini-2.5-pro · gemini-2.5-flash
# Le « or » plutôt que le défaut de getenv : une variable déclarée mais vide
# renvoie "" et laisserait l'agent sans fournisseur.
FOURNISSEUR = (os.getenv("LLM_PROVIDER") or "anthropic").strip().lower()

# ANTHROPIC_MODEL reste accepté : les installations existantes ne cassent pas.
MODELE = (
    os.getenv("LLM_MODEL") or os.getenv("ANTHROPIC_MODEL") or ""
).strip() or modele_par_defaut(FOURNISSEUR)

# Bot de réponses courtes : un effort faible répond plus vite et moins cher.
# Ce réglage n'existe que chez Anthropic ; les autres l'ignorent.
EFFORT = os.getenv("ANTHROPIC_EFFORT", "low").strip()

# Attention : ce plafond ne concerne pas que la réponse visible. Sur les modèles
# actuels le raisonnement interne compte aussi dedans.
MAX_TOKENS = int(os.getenv("ANTHROPIC_MAX_TOKENS") or "4096")

# Nombre maximum d'allers-retours d'outils pour un seul message client.
# Garde-fou contre une boucle où le modèle rappellerait un outil sans fin.
MAX_TOURS_OUTILS = int(os.getenv("MAX_TOURS_OUTILS", "5") or "5")

# Le client est construit une fois, à la première réponse : si la configuration
# est mauvaise, l'erreur doit remonter au moment où l'on tente de répondre, pas
# à l'import — sinon le serveur ne démarre plus et le point de santé ne peut
# rien expliquer.
_client = None


def client_llm():
    global _client
    if _client is None:
        _client = obtenir_client(FOURNISSEUR, MODELE, MAX_TOKENS, EFFORT)
    return _client


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


async def bloc_consignes() -> str:
    """
    Les consignes ponctuelles en vigueur, à ajouter au prompt système.

    C'est l'équivalent du mot laissé à un employé le matin : « plus de tarte au
    citron cette semaine », « on ferme le 15 ». L'agent ne les voit que pendant
    leur période de validité — inutile de penser à les retirer.
    """
    from agent.memory import consignes_actives

    try:
        actives = await consignes_actives()
    except Exception as e:  # noqa: BLE001
        # Une base indisponible ne doit pas empêcher l'agent de répondre.
        logger.error(f"Consignes illisibles, on continue sans : {e}")
        return ""

    if not actives:
        return ""

    lignes = "\n".join(f"- {c.texte}" for c in actives)
    return (
        "\n\n## Consignes en cours\n"
        "Ces instructions ponctuelles priment sur les informations générales "
        "ci-dessus. Applique-les sans les citer explicitement au client.\n"
        f"{lignes}"
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


# Trois façons de satisfaire l'article 50 de l'AI Act (applicable depuis le
# 2 août 2026 : informer la personne « au plus tard lors de la première
# interaction »). Le choix se fait à l'installation.
#
#   discrete   Une ligne en italique à la fin du premier message. Couvre toute
#              la conversation, se remarque à peine. Le meilleur compromis
#              commercial, et c'est le défaut.
#   explicite  Un encart en tête du premier message. Ne laisse aucun doute ;
#              plus froid à la lecture.
#   validation Aucune réponse ne part sans qu'une personne l'ait relue. L'AI Act
#              lève l'obligation de marquage dès lors qu'un humain endosse la
#              responsabilité éditoriale — mais c'est fastidieux au quotidien.
MODE_TRANSPARENCE = (os.getenv("MODE_TRANSPARENCE") or "discrete").strip().lower()

MENTIONS = {
    "discrete": (
        "_Cette conversation est assistée par une intelligence artificielle "
        "pour la rédaction des réponses._"
    ),
    "explicite": (
        "ℹ️ Vous échangez avec un assistant automatique. Demandez « un conseiller » "
        "à tout moment pour joindre quelqu'un de l'équipe."
    ),
}


def message_transparence() -> str:
    """Mention informant le client qu'une IA rédige les réponses."""
    defaut = MENTIONS.get(MODE_TRANSPARENCE, MENTIONS["discrete"])
    return charger_config_prompts().get("message_transparence", defaut)


def appliquer_transparence(texte: str, premier_echange: bool) -> str:
    """
    Ajoute la mention au premier message d'une conversation, et à lui seul.

    La répéter à chaque message serait pénible pour le client sans rien ajouter
    juridiquement : l'article 50 parle de la « première interaction ».
    """
    if not premier_echange or MODE_TRANSPARENCE == "validation":
        return texte
    mention = message_transparence().strip()
    if not mention:
        return texte
    # Discrète : en pied de message, en italique. Explicite : en tête, isolée.
    if MODE_TRANSPARENCE == "explicite":
        return f"{mention}\n\n{texte}"
    return f"{texte}\n\n{mention}"


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
    if not message or len(message.strip()) < 2:
        return message_incompris(), False

    if depenses.depassement():
        logger.error(
            f"Plafond de dépense journalier atteint ({depenses.depense_du_jour} $) : "
            "appel au modèle refusé."
        )
        return message_saturation(), False

    systeme = prompt_systeme() + await bloc_consignes() + horodatage()

    async def executer(nom: str, arguments: dict) -> str:
        # Le numéro vient du webhook, jamais du modèle : sinon une injection de
        # prompt pourrait lui faire enregistrer une demande au nom d'un autre
        # client. On ne l'injecte que dans les outils qui le déclarent, sans quoi
        # les autres lèvent une TypeError et le client perd sa réponse.
        if telephone and outil_accepte(nom, "telephone"):
            arguments = {**arguments, "telephone": telephone}
        logger.info(f"Outil appelé : {nom}")
        return await executer_outil(nom, arguments)

    try:
        bilan = await client_llm().converser(
            systeme=systeme,
            historique=historique,
            message=message,
            outils=schemas_outils(),
            executer=executer,
            max_tours=MAX_TOURS_OUTILS,
        )
    except ErreurLLM as e:
        logger.error(f"Configuration du modèle invalide : {e}")
        return message_erreur(), False
    except Exception as e:  # noqa: BLE001
        logger.error(f"Échec de l'appel au modèle : {e}")
        return message_erreur(), False

    cout = depenses.enregistrer(MODELE, bilan.tokens_entree, bilan.tokens_sortie)
    # Écrit en base tout de suite : sinon le cumul repart de zéro au prochain
    # redéploiement et le plafond journalier ne veut plus rien dire.
    await sauvegarder_depense()
    logger.info(
        f"{FOURNISSEUR}/{MODELE} — {bilan.tokens_entree} entrée / {bilan.tokens_sortie} sortie "
        f"en {bilan.tours} tour(s) (~{cout:.4f} $, cumul du jour {depenses.depense_du_jour} $)"
    )

    if bilan.tronquee:
        logger.warning(
            f"Réponse tronquée au plafond de {MAX_TOKENS} tokens. "
            "Augmentez ANTHROPIC_MAX_TOKENS ou raccourcissez le prompt système."
        )

    if not bilan.texte:
        if bilan.tours >= MAX_TOURS_OUTILS:
            logger.warning(
                f"{MAX_TOURS_OUTILS} tours d'outils atteints pour "
                f"{masquer_telephone(telephone)} sans réponse finale."
            )
            return message_erreur(), False
        logger.warning("Le modèle a renvoyé une réponse sans texte")
        return message_incompris(), False

    return appliquer_transparence(bilan.texte, premier_echange=not historique), True
