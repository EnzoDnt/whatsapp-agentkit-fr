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


# Mots vides du français. Sans eux, abaisser le seuil à deux caractères ferait
# entrer « de », « la », « et », qui figurent sur presque chaque ligne : les 25
# résultats se rempliraient de bruit et la bonne ligne passerait à la trappe.
# Avec eux, on peut enfin chercher les sigles courts — « WC », « PV », « m2 » —
# que l'ancien seuil de trois caractères rendait introuvables.
MOTS_VIDES = frozenset("""
au aux avec ce ces dans de des du elle en et eux il ils je la le les leur lui
ma mais me meme mes moi mon ne nos notre nous on ou par pas pour qu que qui sa
se ses son sur ta te tes toi ton tu un une vos votre vous y est sont etre avoir
ai as ont eu ete cet cette celui ceux dont donc alors aussi comme plus moins
tres bien fait faire dit vers chez sans sous entre apres avant depuis pendant
combien quel quelle quels quelles quand comment pourquoi est-ce
""".split())


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
    """
    Recherche plein texte simple dans les fichiers de knowledge/.

    La recherche est littérale et travaille LIGNE PAR LIGNE : elle renvoie les
    lignes qui contiennent les mots de la requête. Deux conséquences pour qui
    rédige les documents de knowledge/ :

    - Un fait doit tenir sur UNE seule ligne. Si l'intitulé et le prix sont
      séparés par un retour à la ligne, la recherche renvoie l'un sans l'autre,
      et le modèle complète le vide — c'est-à-dire invente un tarif.
    - Il n'y a pas de racinisation : « livrer » ne trouve pas « livraison ».
      Écrivez les variantes utiles sur la même ligne, accentuées et non
      accentuées.
    """
    if not DOSSIER_KNOWLEDGE.is_dir():
        return "Aucun document d'entreprise disponible."

    mots = [
        m
        for m in re.split(r"\W+", requete.lower())
        if len(m) >= 2 and m not in MOTS_VIDES
    ]
    if not mots:
        return f"Aucune information trouvée pour '{requete}' dans les documents."

    # (nombre de mots distincts trouvés, ligne) : une ligne qui répond à
    # « pain sans gluten » sur les trois mots passe avant celle qui n'a que
    # « pain ». Sans ce classement, le plafond de 25 lignes se remplit dans
    # l'ordre des fichiers et la bonne réponse peut rester dehors.
    trouvailles: list[tuple[int, str]] = []

    for chemin in sorted(DOSSIER_KNOWLEDGE.iterdir()):
        if chemin.name.startswith(".") or not chemin.is_file():
            continue
        try:
            contenu = chemin.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for ligne in contenu.splitlines():
            if not ligne.strip():
                continue
            minuscule = ligne.lower()
            touches = sum(1 for m in mots if m in minuscule)
            if touches:
                trouvailles.append((touches, ligne.strip()))

    if not trouvailles:
        return f"Aucune information trouvée pour '{requete}' dans les documents."

    trouvailles.sort(key=lambda t: t[0], reverse=True)
    return "\n".join(ligne for _, ligne in trouvailles[:25])


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
            "Cette heure est une BORNE TECHNIQUE, pas une proposition : ne la "
            "cite pas telle quelle au client (« à partir de 16h02 » ne veut "
            "rien dire pour lui). Propose le premier créneau d'ouverture qui "
            "commence après cette borne."
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
    # Cette chaîne est lue par le modèle, pas par un humain : elle dicte
    # littéralement le dernier message reçu par le client. Formulée seulement
    # comme « dis qu'un humain revient », elle produit exactement cela — et
    # efface tout le reste, y compris les consignes de sécurité déjà rédigées.
    # Observé en conditions réelles : sur une odeur de gaz, le client recevait
    # une phrase d'attente à la place de « coupez le compteur, appelez GRDF ».
    return (
        f"Escalade #{ref} transmise à l'équipe. Tu ne réponds plus sur cette "
        "conversation après ce message.\n"
        "Ton message au client doit contenir, dans cet ordre : "
        "(1) un récapitulatif en une phrase de ce que tu as compris ; "
        "(2) les consignes de sécurité ou gestes d'urgence s'il y en a — ils ne "
        "sont JAMAIS supprimés, la sécurité prime sur la concision ; "
        "(3) ce qui manque encore, le cas échéant ; "
        "(4) enfin, qu'un membre de l'équipe revient vers lui, sans annoncer "
        "de délai.\n"
        "Ne réponds jamais par la seule phrase d'attente : un client qui vient "
        "de décrire sa situation croirait ne pas avoir été lu."
    )
