"""
Outils métier de l'agent — réellement exécutés par Claude.

Différence majeure avec le kit d'origine : là-bas, ce fichier ne contenait que
des fonctions jamais appelées. L'agent *parlait* d'enregistrer une commande sans
rien enregistrer nulle part. Ici, chaque fonction décorée par @outil est déclarée
à Claude et exécutée pour de vrai (voir la boucle dans brain.py).

Pour ajouter un outil : écrivez une fonction, décorez-la, c'est tout.
"""

from __future__ import annotations

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


def executer_outil(nom: str, arguments: dict) -> str:
    """
    Exécute un outil et retourne un résultat textuel pour Claude.

    Toute exception est capturée et renvoyée comme message d'erreur : une
    exception qui remonterait ferait perdre le tour de conversation au client.
    """
    entree = _REGISTRE.get(nom)
    if entree is None:
        return f"Erreur : outil inconnu '{nom}'."
    _, fn = entree
    try:
        resultat = fn(**arguments)
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
def enregistrer_demande(
    nom_client: str, details: str, date_souhaitee: str, telephone: str = ""
) -> str:
    """Écrit la demande sur disque, horodatée, pour reprise par l'équipe."""
    DOSSIER_DONNEES.mkdir(exist_ok=True)
    demande: dict[str, Any] = {
        "nom_client": nom_client,
        "details": details,
        "date_souhaitee": date_souhaitee,
        "telephone": telephone,
        "recue_le": datetime.now().isoformat(timespec="seconds"),
        "statut": "a_traiter",
    }
    fichier = DOSSIER_DONNEES / f"demande-{datetime.now():%Y%m%d-%H%M%S}.json"
    fichier.write_text(json.dumps(demande, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Demande enregistrée : {fichier.name}")
    return (
        f"Demande enregistrée sous la référence {fichier.stem}. "
        "Confirmez au client que l'équipe revient vers lui pour valider."
    )


@outil(
    description=(
        "Signale qu'un humain doit reprendre la conversation : réclamation, cas "
        "sensible, ou question hors de votre périmètre."
    ),
    schema={
        "type": "object",
        "properties": {
            "motif": {"type": "string", "description": "Pourquoi un humain est nécessaire."},
            "telephone": {"type": "string", "description": "Numéro du client, transmis par le système."},
        },
        "required": ["motif"],
        "additionalProperties": False,
    },
)
def transferer_a_humain(motif: str, telephone: str = "") -> str:
    DOSSIER_DONNEES.mkdir(exist_ok=True)
    fichier = DOSSIER_DONNEES / f"escalade-{datetime.now():%Y%m%d-%H%M%S}.json"
    fichier.write_text(
        json.dumps(
            {
                "motif": motif,
                "telephone": telephone,
                "signalee_le": datetime.now().isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info(f"Escalade vers un humain : {motif[:60]}")
    return (
        "Transfert signalé à l'équipe. Dites au client qu'un membre de l'équipe "
        "le recontacte, sans promettre de délai précis."
    )
