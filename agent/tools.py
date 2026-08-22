"""
Outils métier de l'agent — réellement exécutés par Claude.

Différence majeure avec le kit d'origine : là-bas, ce fichier ne contenait que
des fonctions jamais appelées. L'agent *parlait* d'enregistrer une commande sans
rien enregistrer nulle part. Ici, chaque fonction décorée par @outil est déclarée
à Claude et exécutée pour de vrai (voir la boucle dans brain.py).

Pour ajouter un outil : écrivez une fonction, décorez-la, c'est tout.
"""

from __future__ import annotations

import inspect
import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import yaml

logger = logging.getLogger("agentkit")

DOSSIER_KNOWLEDGE = Path("knowledge")
DOSSIER_DONNEES = Path("donnees")

# Registre : nom -> (schéma exposé à Claude, fonction Python)
_REGISTRE: dict[str, tuple[dict, Callable]] = {}


def outil(description: str, schema: dict):
    """Déclare une fonction comme outil utilisable par Claude."""

    def decorateur(fn: Callable) -> Callable:
        _REGISTRE[fn.__name__] = (
            {"name": fn.__name__, "description": description, "input_schema": schema},
            fn,
        )
        return fn

    return decorateur


def schemas_outils() -> list[dict]:
    """Définitions à passer au paramètre `tools` de l'API."""
    return [schema for schema, _ in _REGISTRE.values()]


def outil_accepte(nom: str, parametre: str) -> bool:
    """
    L'outil déclare-t-il ce paramètre ?

    Sert à n'injecter le numéro du client que dans les outils qui l'attendent :
    l'ajouter partout provoque un TypeError sur les autres, et le client perd
    sa réponse sans que rien n'indique pourquoi.
    """
    entree = _REGISTRE.get(nom)
    if entree is None:
        return False
    return parametre in (entree[0]["input_schema"].get("properties") or {})


async def executer_outil(nom: str, arguments: dict) -> str:
    """
    Exécute un outil et retourne un résultat textuel pour Claude.

    Accepte indifféremment des fonctions normales et des coroutines : certains
    outils doivent écrire en base, ce qui est asynchrone.

    Toute exception est capturée et renvoyée comme message d'erreur : une
    exception qui remonterait ferait perdre son tour de conversation au client.
    """
    entree = _REGISTRE.get(nom)
    if entree is None:
        return f"Erreur : outil inconnu '{nom}'."
    _, fn = entree
    try:
        resultat = fn(**arguments)
        if inspect.isawaitable(resultat):
            resultat = await resultat
    except TypeError as e:
        return f"Erreur d'arguments pour '{nom}' : {e}"
    except Exception as e:  # noqa: BLE001
        logger.exception(f"L'outil '{nom}' a échoué")
        return f"Erreur pendant l'exécution de '{nom}' : {e}"
    return resultat if isinstance(resultat, str) else json.dumps(resultat, ensure_ascii=False)


# ── Configuration métier ─────────────────────────────────────────────────


def charger_config_metier() -> dict:
    try:
        with open("config/entreprise.yaml", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.error("config/entreprise.yaml introuvable")
        return {}


# ── Outils ───────────────────────────────────────────────────────────────


@outil(
    description=(
        "Recherche une information dans les documents de l'entreprise (tarifs, "
        "catalogue, FAQ, conditions). À utiliser dès qu'une question porte sur un "
        "détail qui pourrait figurer dans ces documents plutôt que d'improviser."
    ),
    schema={
        "type": "object",
        "properties": {
            "requete": {
                "type": "string",
                "description": "Mots-clés à chercher, par exemple 'tarif entremets' ou 'allergènes'.",
            }
        },
        "required": ["requete"],
        "additionalProperties": False,
    },
)
def rechercher_information(requete: str) -> str:
    """Recherche plein texte simple dans les fichiers de knowledge/."""
    if not DOSSIER_KNOWLEDGE.is_dir():
        return "Aucun document d'entreprise disponible."

    mots = [m for m in re.split(r"\W+", requete.lower()) if len(m) > 2]
    trouvailles: list[str] = []

    for chemin in sorted(DOSSIER_KNOWLEDGE.iterdir()):
        if chemin.name.startswith(".") or not chemin.is_file():
            continue
        try:
            contenu = chemin.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for ligne in contenu.splitlines():
            if any(m in ligne.lower() for m in mots) and ligne.strip():
                trouvailles.append(ligne.strip())

    if not trouvailles:
        return f"Aucune information trouvée pour '{requete}' dans les documents."
    return "\n".join(trouvailles[:25])


@outil(
    description=(
        "Vérifie qu'une date de retrait ou de rendez-vous respecte le délai minimum "
        "exigé pour ce type de prestation. À appeler AVANT de confirmer une date au client."
    ),
    schema={
        "type": "object",
        "properties": {
            "type_prestation": {
                "type": "string",
                "description": "Clé du délai, telle que définie dans config/entreprise.yaml (ex. 'ceremonie').",
            },
            "date_souhaitee": {
                "type": "string",
                "description": "Date et heure souhaitées, au format AAAA-MM-JJ HH:MM.",
            },
        },
        "required": ["type_prestation", "date_souhaitee"],
        "additionalProperties": False,
    },
)
def verifier_delai(type_prestation: str, date_souhaitee: str) -> str:
    """
    Le délai est vérifié en CODE, pas seulement dans le prompt.

    Un modèle peut accepter par complaisance une commande que l'atelier ne peut
    pas honorer. Ici la règle métier est arithmétique : elle ne se négocie pas.
    """
    delais = charger_config_metier().get("delais_heures", {})
    heures = int(delais.get(type_prestation, 0))

    try:
        souhaitee = datetime.fromisoformat(date_souhaitee.strip().replace("/", "-"))
    except ValueError:
        return "Date incomprise. Format attendu : AAAA-MM-JJ HH:MM."

    minimum = datetime.now() + timedelta(hours=heures)
    if souhaitee < minimum:
        return (
            f"REFUSÉ : '{type_prestation}' exige {heures} h de délai. "
            f"La date la plus proche possible est le {minimum:%d/%m/%Y à %H:%M}. "
            "Proposez cette date ou une date ultérieure au client."
        )
    return f"ACCEPTÉ : le {souhaitee:%d/%m/%Y à %H:%M} respecte le délai de {heures} h."


@outil(
    description=(
        "Enregistre une demande client (commande, réservation, rendez-vous) pour que "
        "l'équipe la traite. À n'appeler qu'une fois TOUS les éléments réunis et "
        "confirmés par le client."
    ),
    schema={
        "type": "object",
        "properties": {
            "nom_client": {"type": "string", "description": "Nom donné par le client."},
            "details": {"type": "string", "description": "Ce qui est demandé, en clair."},
            "date_souhaitee": {"type": "string", "description": "Date et heure de retrait ou de rendez-vous."},
            "telephone": {"type": "string", "description": "Numéro du client, transmis par le système."},
        },
        "required": ["nom_client", "details", "date_souhaitee"],
        "additionalProperties": False,
    },
)
async def enregistrer_demande(
    nom_client: str, details: str, date_souhaitee: str, telephone: str = ""
) -> str:
    """Enregistre la demande en base pour qu'elle apparaisse dans le back-office."""
    from agent.memory import enregistrer_demande_db

    ref = await enregistrer_demande_db(
        identifiant=telephone,
        nom_client=nom_client,
        details=details,
        date_souhaitee=date_souhaitee,
    )
    logger.info(f"Demande #{ref} enregistrée pour {nom_client}")
    return (
        f"Demande enregistrée sous la référence #{ref}. "
        "Confirme au client que l'équipe revient vers lui pour valider."
    )


@outil(
    description=(
        "Passe la main à l'équipe humaine. À utiliser dans quatre cas : (1) le client "
        "demande explicitement à parler à quelqu'un, (2) il montre de l'agacement ou "
        "de la colère, (3) il répète la même demande sans que tu parviennes à l'aider, "
        "(4) il te manque une information que tu n'as pas le droit d'inventer. "
        "Fournis TOUJOURS une réponse_proposee : l'équipe la validera d'un clic plutôt "
        "que de tout réécrire. Après cet appel, dis simplement au client que quelqu'un "
        "de l'équipe revient vers lui — sans promettre de délai."
    ),
    schema={
        "type": "object",
        "properties": {
            "motif": {
                "type": "string",
                "description": "Pourquoi tu passes la main, en une phrase, pour l'équipe.",
            },
            "question_equipe": {
                "type": "string",
                "description": (
                    "La question précise que tu poses à l'équipe. Laisse vide si tu "
                    "n'as besoin de rien et que tu passes la main pour une autre raison."
                ),
            },
            "reponse_proposee": {
                "type": "string",
                "description": (
                    "Le message que tu enverrais au client si tu avais l'information. "
                    "L'équipe le validera, le corrigera, ou écrira le sien."
                ),
            },
            "urgence": {
                "type": "string",
                "enum": ["normale", "haute"],
                "description": "« haute » si le client est mécontent ou s'il y a urgence.",
            },
            "telephone": {"type": "string", "description": "Fourni par le système."},
        },
        "required": ["motif"],
        "additionalProperties": False,
    },
)
async def passer_la_main(
    motif: str,
    question_equipe: str = "",
    reponse_proposee: str = "",
    urgence: str = "normale",
    telephone: str = "",
) -> str:
    """
    Crée une escalade et met la conversation en attente humaine.

    L'agent se tait ensuite sur cette conversation : c'est le point important.
    Une escalade qui laisse le robot continuer à répondre par-dessus l'humain
    est pire que pas d'escalade du tout.
    """
    from agent.memory import basculer_pause_conversation, enregistrer_escalade

    ref = await enregistrer_escalade(
        identifiant=telephone,
        motif=motif,
        question_equipe=question_equipe,
        reponse_proposee=reponse_proposee,
        urgence=urgence,
    )
    if telephone:
        await basculer_pause_conversation(telephone, True)

    logger.info(f"Escalade #{ref} ({urgence}) : {motif[:70]}")
    return (
        f"Escalade #{ref} transmise à l'équipe. Tu ne réponds plus sur cette "
        "conversation. Dis au client qu'un membre de l'équipe revient vers lui, "
        "sans annoncer de délai."
    )
