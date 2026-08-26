"""
Génération et publication des documents juridiques.

L'agent a déjà une URL HTTPS publique : il sert donc lui-même sa politique de
confidentialité, ses conditions d'utilisation et ses instructions de
suppression. Meta exige ces URL dans les paramètres de l'app ; les faire vivre
avec le logiciel évite une page à maintenir ailleurs, qui se désynchronise
toujours de la configuration réelle.

Ce qui est déjà connu du kit n'est jamais redemandé : la durée de conservation,
le fournisseur d'IA et le mode de transparence sont lus dans l'environnement.
Une politique qui annonce 90 jours pendant que RETENTION_JOURS en vaut 30 est
pire qu'une absence de politique.
"""

from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

FICHIER = Path("config/juridique.yaml")

# Sous-traitants réellement mobilisés par le kit, selon la configuration.
# Les nommer est une obligation (art. 13 RGPD), et c'est ce que les gabarits
# du commerce ne peuvent pas deviner à votre place.
SOUS_TRAITANTS = {
    "meta": (
        "Meta Platforms Ireland Ltd",
        "acheminement des messages WhatsApp",
        "Irlande (UE)",
    ),
    "anthropic": (
        "Anthropic PBC",
        "génération des réponses",
        "États-Unis — clauses contractuelles types",
    ),
    "openai": (
        "OpenAI Ireland Ltd",
        "génération des réponses et transcription des fichiers",
        "Irlande (UE) et États-Unis — clauses contractuelles types",
    ),
    "google": (
        "Google Ireland Ltd",
        "génération des réponses et analyse des fichiers",
        "Irlande (UE) et États-Unis — clauses contractuelles types",
    ),
    "openrouter": (
        "OpenRouter, Inc.",
        "acheminement des requêtes vers le modèle",
        "États-Unis — clauses contractuelles types",
    ),
}


# Autorités de contrôle, par pays. Une politique de confidentialité doit
# indiquer où réclamer (art. 13 RGPD) : une URL inventée est pire qu'absente,
# elle envoie la personne dans le vide au moment où elle exerce un droit.
# L'assistant d'installation lit cette table au lieu de deviner.
AUTORITES = {
    "FR": ("CNIL", "https://www.cnil.fr/fr/plaintes", "RGPD + loi Informatique et Libertés"),
    "BE": ("Autorité de protection des données (APD)",
           "https://www.autoriteprotectiondonnees.be", "RGPD"),
    "LU": ("Commission nationale pour la protection des données (CNPD)",
           "https://cnpd.public.lu", "RGPD"),
    "DE": ("l'autorité de protection des données de votre Land",
           "https://www.bfdi.bund.de/DE/Service/Anschriften/anschriften_node.html",
           "RGPD + BDSG — Impressum plus exigeant qu'en France"),
    "ES": ("Agencia Española de Protección de Datos (AEPD)",
           "https://www.aepd.es", "RGPD + LSSI-CE"),
    "IT": ("Garante per la protezione dei dati personali",
           "https://www.garanteprivacy.it", "RGPD"),
    "GB": ("Information Commissioner's Office (ICO)",
           "https://ico.org.uk/make-a-complaint/", "UK GDPR + Data Protection Act 2018"),
    "CH": ("Préposé fédéral à la protection des données et à la transparence (PFPDT)",
           "https://www.edoeb.admin.ch", "nLPD — et RGPD en plus si clients dans l'UE"),
    "CA-QC": ("Commission d'accès à l'information du Québec (CAI)",
              "https://www.cai.gouv.qc.ca", "Loi 25 — plus exigeante que le RGPD sur le consentement"),
    "CA": ("Commissariat à la protection de la vie privée du Canada",
           "https://www.priv.gc.ca", "LPRPDE / PIPEDA"),
    "BR": ("Autoridade Nacional de Proteção de Dados (ANPD)",
           "https://www.gov.br/anpd", "LGPD"),
    "US": ("California Privacy Protection Agency (si vos clients sont en Californie)",
           "https://cppa.ca.gov", "CCPA/CPRA + lois d'État — pas de loi fédérale"),
}

# Pays où les gabarits du kit ne suffisent pas : ils demandent une refonte,
# pas un ajustement. L'assistant doit le dire AVANT de générer, pas après.
ZONES_A_REFONDRE = {
    "CA-QC": "La Loi 25 impose un responsable de la protection des renseignements "
             "personnels, une évaluation des facteurs relatifs à la vie privée et un "
             "consentement exprès pour tout usage secondaire. Les gabarits sont à refondre.",
    "US": "Il n'existe pas de loi fédérale : la CCPA/CPRA et une vingtaine "
          "d'équivalents d'État s'appliquent selon des seuils différents. Les mentions "
          "« Do Not Sell or Share » sont absentes des gabarits, et le TCPA sanctionne "
          "lourdement les messages non sollicités.",
}

# Hébergeurs courants. La LCEN française impose de nommer l'hébergeur dans les
# mentions légales, avec une adresse joignable.
HEBERGEURS = {
    "hetzner": ("Hetzner Online GmbH",
                "Industriestr. 25, 91710 Gunzenhausen, Allemagne", "Allemagne"),
    "scaleway": ("Scaleway SAS",
                 "8 rue de la Ville l'Évêque, 75008 Paris, France", "France"),
    "ovh": ("OVH SAS", "2 rue Kellermann, 59100 Roubaix, France", "France"),
    "infomaniak": ("Infomaniak Network SA",
                   "Rue Eugène-Marziano 25, 1227 Genève, Suisse", "Suisse"),
    "railway": ("Railway Corp.", "États-Unis", "États-Unis"),
    "render": ("Render Services, Inc.", "États-Unis", "États-Unis"),
    "fly": ("Fly.io, Inc.", "États-Unis", "États-Unis"),
}


class ErreurJuridique(RuntimeError):
    """Configuration juridique absente ou inexploitable."""


def charger() -> dict | None:
    """La configuration juridique, ou None si elle n'a pas été renseignée."""
    if not FICHIER.exists():
        return None
    try:
        return yaml.safe_load(FICHIER.read_text(encoding="utf-8")) or None
    except yaml.YAMLError as e:
        logger.error(f"config/juridique.yaml illisible : {e}")
        return None


def contexte(conf: dict) -> dict:
    """
    Assemble ce qui est déclaré et ce que le kit sait déjà.

    Le second l'emporte : c'est la configuration réellement en vigueur.
    """
    retention = int(os.getenv("RETENTION_JOURS", "90") or "90")
    fournisseur = (os.getenv("LLM_PROVIDER", "anthropic") or "anthropic").strip().lower()

    tiers = [SOUS_TRAITANTS["meta"]]
    if fournisseur in SOUS_TRAITANTS:
        tiers.append(SOUS_TRAITANTS[fournisseur])
    # Un service distinct peut traiter les fichiers reçus : il doit être cité.
    for var in ("MEDIA_AUDIO_FOURNISSEUR", "MEDIA_IMAGE_FOURNISSEUR",
                "MEDIA_VIDEO_FOURNISSEUR", "MEDIA_DOCUMENT_FOURNISSEUR"):
        f = (os.getenv(var, "") or "").strip().lower()
        if f in SOUS_TRAITANTS and SOUS_TRAITANTS[f] not in tiers:
            tiers.append(SOUS_TRAITANTS[f])

    heb = conf.get("hebergeur") or {}
    if heb.get("nom"):
        tiers.append((heb["nom"], "hébergement de l'agent et de sa base",
                      heb.get("pays_donnees", "non précisé")))

    revue = conf.get("revue_juridique") or {}
    return {
        **conf,
        "retention_jours": retention,
        "retention_texte": "jamais purgé" if retention == 0 else f"{retention} jours",
        "fournisseur_ia": fournisseur,
        "mode_transparence": (os.getenv("MODE_TRANSPARENCE", "discrete") or "discrete"),
        "journalise_contenu": (os.getenv("LOG_MESSAGE_CONTENT", "") or "").lower() == "true",
        "sous_traitants": tiers,
        "revue_faite": bool(revue.get("effectuee")),
        "revue_par": revue.get("par") or "",
        "revue_date": revue.get("date") or "",
        "genere_le": date.today().isoformat(),
    }


def _bandeau(c: dict) -> str:
    """Avertissement affiché tant qu'aucun professionnel n'a relu les textes."""
    if c["revue_faite"]:
        return ""
    return (
        "> ⚠️ **Document non relu par un professionnel du droit.**\n"
        "> Ce texte a été généré à partir d'un gabarit. Il doit être relu par un\n"
        "> juriste ou un avocat avant d'être opposé à qui que ce soit. En l'état,\n"
        "> il ne constitue pas un avis juridique.\n\n"
    )


def _pied(c: dict) -> str:
    revue = ""
    if c["revue_faite"] and c["revue_par"]:
        revue = f" · Relu par {c['revue_par']}"
        if c["revue_date"]:
            revue += f" le {c['revue_date']}"
    maj = (c.get("publication") or {}).get("derniere_revision") or c["genere_le"]
    return f"\n\n---\n\n*Dernière mise à jour : {maj}{revue}.*\n"


def _tableau_tiers(c: dict) -> str:
    lignes = ["| Destinataire | Rôle | Localisation des données |",
              "|---|---|---|"]
    for nom, role, pays in c["sous_traitants"]:
        lignes.append(f"| {nom} | {role} | {pays} |")
    return "\n".join(lignes)


def politique_confidentialite(c: dict) -> str:
    e = c["entreprise"]
    t = c["traitement"]
    pd = c["protection_donnees"]
    j = c["juridiction"]

    bases = {
        "interet_legitime": "l'intérêt légitime (article 6.1.f du RGPD) : vous nous "
                            "écrivez, nous vous répondons",
        "contrat": "l'exécution d'un contrat ou de mesures précontractuelles "
                   "(article 6.1.b du RGPD)",
        "consentement": "votre consentement (article 6.1.a du RGPD)",
    }
    base = bases.get(t.get("base_legale", ""), t.get("base_legale", "non précisée"))

    finalites = "\n".join(f"- {f}" for f in t.get("finalites", []))
    donnees = "\n".join(f"- {d}" for d in t.get("donnees_traitees", []))

    journaux = (
        "Le contenu de vos messages est écrit dans les journaux techniques. "
        "**Cette option est réservée au développement** et ne devrait pas être "
        "active en production."
        if c["journalise_contenu"]
        else "Le contenu de vos messages n'est jamais écrit dans les journaux "
             "techniques. Votre numéro y apparaît uniquement sous forme "
             "d'empreinte, qui ne permet pas de le reconstituer."
    )

    conservation = (
        "Aucune purge automatique n'est configurée. C'est déconseillé : "
        "précisez une durée de conservation."
        if c["retention_jours"] == 0
        else f"L'historique de vos conversations est effacé automatiquement "
             f"**{c['retention_texte']}** après le dernier message."
    )

    return f"""# Politique de confidentialité

{_bandeau(c)}## Qui traite vos données

{e['raison_sociale']}, {e['forme_juridique']}, {e['immatriculation']}.
{e['adresse']}.

Pour toute question relative à vos données : **{pd['email']}**{
    ", " + pd['telephone'] if pd.get('telephone') else ""}.
{"Un délégué à la protection des données a été désigné : " + pd['nom'] + "."
 if pd.get('dpo_designe') else "Responsable du traitement : " + pd.get('nom', '') + "."}

## Ce que nous traitons, et pourquoi

Lorsque vous écrivez à notre numéro WhatsApp, nous traitons :

{donnees}

Ces données servent exclusivement à :

{finalites}

La base légale de ce traitement est {base}.

Nous ne vendons aucune donnée, nous ne faisons ni prospection ni publicité à
partir de vos messages, et nous ne construisons aucun profil publicitaire.

## Comment vous nous avez donné votre accord

{(t.get('opt_in') or 'Vous initiez la conversation.').capitalize().rstrip('.')}.

Ce contact vaut accord pour que nous vous répondions. Il ne vaut **pas** accord
pour recevoir des messages promotionnels : ceux-ci supposent un consentement
distinct, que vous pouvez retirer à tout moment.

## Une intelligence artificielle rédige les réponses

Les réponses sont rédigées par un système d'intelligence artificielle, et vous
en êtes informé dès le premier message. Vous pouvez à tout moment demander à
parler à une personne : écrivez-le simplement, la conversation est transmise.

Aucune décision produisant des effets juridiques à votre égard n'est prise de
façon automatisée au sens de l'article 22 du RGPD. L'agent renseigne, prend des
demandes et transmet ; il ne décide pas seul.

Le contenu de vos messages est transmis au fournisseur du modèle pour produire
la réponse. Il n'est pas utilisé pour entraîner des modèles.

## Qui d'autre voit ces données

{_tableau_tiers(c)}

Ces prestataires agissent sur nos instructions et sont liés par des engagements
de confidentialité. Les transferts hors Union européenne, lorsqu'ils existent,
sont encadrés par les clauses contractuelles types de la Commission européenne.

## Combien de temps

{conservation}

{journaux}

## Vos droits

Vous disposez d'un droit d'accès, de rectification, d'effacement, de limitation,
d'opposition et de portabilité sur vos données.

**Pour exercer ces droits, écrivez à {pd['email']}.** Nous répondons sous un
mois. La marche à suivre pour obtenir l'effacement est détaillée sur notre page
[Suppression de vos données]({(c.get('publication') or {}).get('url_publique', '')}/legal/suppression).

Vous pouvez également écrire « supprimez mes données » directement dans la
conversation WhatsApp.

Si notre réponse ne vous satisfait pas, vous pouvez saisir la {j['autorite_controle']} :
{j.get('autorite_controle_url', '')}

## WhatsApp

Les conversations transitent par WhatsApp, service de Meta. L'usage que Meta
fait des données de ses utilisateurs relève de sa propre politique de
confidentialité, sur laquelle nous n'avons aucune maîtrise. Nous n'accédons
qu'aux messages que vous nous adressez.
{_pied(c)}"""


def suppression_donnees(c: dict) -> str:
    e = c["entreprise"]
    pd = c["protection_donnees"]
    return f"""# Suppression de vos données

{_bandeau(c)}Vous pouvez à tout moment demander la suppression des données que
{e['raison_sociale']} détient sur vous. Voici comment, et ce qui se passe ensuite.

## Trois façons de demander

**Dans la conversation.** Écrivez « supprimez mes données » à notre numéro
WhatsApp. La demande est transmise à notre équipe.

**Par courriel.** Écrivez à **{pd['email']}** depuis l'adresse de votre choix,
en indiquant le numéro de téléphone concerné.

**Par courrier.** {e['raison_sociale']}, {e['adresse']}.

## Ce que nous supprimons

- L'historique complet de vos conversations avec l'agent
- Les fichiers que vous nous avez envoyés : photos, notes vocales, documents
- Les demandes enregistrées à votre nom
- L'empreinte de votre numéro dans nos journaux techniques

## Ce que nous devons conserver

Certaines données ne peuvent pas être effacées immédiatement lorsqu'une
obligation légale l'impose : une facture est conservée dix ans au titre du code
de commerce, indépendamment de votre demande. Nous vous indiquons précisément ce
qui est concerné dans notre réponse.

## Délais

Nous accusons réception sous 72 heures et procédons à l'effacement **sous un
mois**, conformément à l'article 12.3 du RGPD. Si la demande est complexe, ce
délai peut être prolongé de deux mois ; nous vous en informons alors avec le
motif.

À l'issue de l'opération, nous vous confirmons par écrit ce qui a été supprimé.

## Suppression automatique

Indépendamment de toute demande, l'historique des conversations est
{"conservé sans limite de durée — configuration déconseillée" if c["retention_jours"] == 0
 else f"effacé automatiquement {c['retention_texte']} après le dernier message"}.

## En cas de désaccord

Si vous estimez que votre demande n'a pas été traitée correctement, vous pouvez
saisir la {c['juridiction']['autorite_controle']} :
{c['juridiction'].get('autorite_controle_url', '')}
{_pied(c)}"""


def conditions_utilisation(c: dict) -> str:
    e = c["entreprise"]
    j = c["juridiction"]
    return f"""# Conditions d'utilisation du service de messagerie

{_bandeau(c)}Ces conditions encadrent l'usage de l'assistant WhatsApp mis à
disposition par {e['raison_sociale']}. En écrivant à notre numéro, vous en
acceptez les termes.

## Ce que fait ce service

Un assistant conversationnel répond à vos questions sur nos produits, nos
horaires et nos tarifs, enregistre vos demandes, et transmet à notre équipe ce
qui le nécessite. Il est **gratuit** et réservé à un usage personnel, dans le
cadre de votre relation avec {e['raison_sociale']}.

Ce n'est pas un assistant généraliste : il ne répond que sur notre activité.

## Ce qu'il n'est pas

Les réponses sont fournies à titre informatif. **Elles ne constituent ni un
devis, ni un engagement contractuel, ni un conseil professionnel.** Seul un
document signé par {e['raison_sociale']} engage l'entreprise.

Un tarif communiqué par l'assistant est indicatif et peut évoluer. En cas de
divergence entre une réponse de l'assistant et un document commercial, **le
document prévaut**.

L'assistant n'est pas un service d'urgence. En cas d'urgence, appelez
{e.get('telephone', 'notre standard')} ou les services compétents.

## Ce que nous attendons de vous

- N'envoyez pas de données sensibles : santé, opinions, coordonnées bancaires.
  Nous n'en demandons jamais par messagerie.
- N'utilisez pas le service pour des contenus illicites, injurieux ou
  harcelants.
- N'essayez pas de détourner l'assistant de son objet, ni d'en perturber le
  fonctionnement.

Nous pouvons interrompre l'accès en cas d'usage abusif, sans préavis.

## Disponibilité

Le service est fourni « en l'état », sans garantie de disponibilité continue.
Une interruption technique ne saurait engager notre responsabilité. Nous
limitons le nombre de messages traités par correspondant sur une période donnée
afin d'assurer le service à tous.

## Responsabilité

{e['raison_sociale']} ne peut être tenue responsable d'un dommage résultant
d'une réponse inexacte ou incomplète de l'assistant, dès lors que vous
disposiez de la possibilité de vérifier l'information auprès de notre équipe.
Cette limitation ne s'applique pas en cas de faute lourde ou dolosive, ni aux
dommages corporels.

## Vos données

Le traitement de vos données est décrit dans notre
[politique de confidentialité]({(c.get('publication') or {}).get('url_publique','')}/legal/confidentialite).

## Évolution et droit applicable

Ces conditions peuvent être modifiées ; la version applicable est celle publiée
à cette adresse au moment de votre message. Elles sont soumises au
{j['droit_applicable']}. À défaut de résolution amiable, compétence est
attribuée aux {j['tribunal']}.
{_pied(c)}"""


def mentions_legales(c: dict) -> str:
    e = c["entreprise"]
    h = c["hebergeur"]
    tva = f"\nNuméro de TVA intracommunautaire : {e['tva_intracommunautaire']}" if e.get("tva_intracommunautaire") else ""
    site = f"\nSite : {e['site_web']}" if e.get("site_web") else ""
    return f"""# Mentions légales

{_bandeau(c)}## Éditeur

{e['raison_sociale']}
{e['forme_juridique']}
{e['immatriculation']}{tva}
{e['adresse']}
Téléphone : {e.get('telephone', '')}
Courriel : {e.get('email', '')}{site}

Directeur de la publication : {e.get('representant_legal', '')}

## Hébergeur

{h['nom']}
{h['adresse']}
{h.get('telephone', '')}

Les données de l'agent sont hébergées en **{h.get('pays_donnees', 'non précisé')}**.

## Propriété intellectuelle

Les contenus diffusés par l'assistant — textes, tarifs, documents — sont la
propriété de {e['raison_sociale']}. Toute reproduction sans autorisation est
interdite.

## Signalement

Pour signaler un contenu illicite diffusé par ce service, écrivez à
{c['protection_donnees']['email']} en précisant la date et le contenu concerné.
{_pied(c)}"""


def transparence_ia(c: dict) -> str:
    e = c["entreprise"]
    modes = {
        "discrete": "une mention en fin de premier message",
        "explicite": "un encart en tête du premier message",
        "validation": "une relecture humaine avant tout envoi",
    }
    mention = modes.get(c["mode_transparence"], c["mode_transparence"])
    return f"""# Information sur l'usage de l'intelligence artificielle

{_bandeau(c)}## Vous parlez à une machine, et vous en êtes informé

Les réponses de l'assistant WhatsApp de {e['raison_sociale']} sont rédigées par
un système d'intelligence artificielle. Vous en êtes averti par {mention}.

Cette information répond à l'article 50 du règlement européen sur
l'intelligence artificielle, applicable depuis le 2 août 2026, qui impose
d'informer une personne lorsqu'elle interagit avec un système d'IA.

## Ce que le système fait, et ne fait pas

Il consulte nos documents internes pour répondre, enregistre des demandes, et
transmet à un humain ce qui le dépasse. Il **ne prend aucune décision** ayant
des effets juridiques ou vous affectant de manière significative, au sens de
l'article 22 du RGPD.

Un système d'IA peut se tromper ou formuler une réponse inexacte. Les
informations importantes — un prix, un délai, un engagement — doivent être
confirmées par notre équipe.

## Parler à une personne

Écrivez-le simplement dans la conversation. La demande est transmise, et
l'assistant cesse de répondre sur cette conversation le temps que quelqu'un
reprenne la main.

## Le modèle utilisé

Les réponses sont produites par un modèle de langage fourni par un prestataire
tiers, désigné dans notre
[politique de confidentialité]({(c.get('publication') or {}).get('url_publique','')}/legal/confidentialite).
Vos messages lui sont transmis pour produire la réponse, et ne servent pas à
entraîner ses modèles.
{_pied(c)}"""


def annexe_sous_traitance(c: dict) -> str:
    """Générée uniquement en mode agence (article 28 du RGPD)."""
    i = c.get("integrateur") or {}
    e = c["entreprise"]
    return f"""# Annexe — traitement des données en sous-traitance

{_bandeau(c)}Cette annexe précise la répartition des rôles entre
{e['raison_sociale']} et {i.get('raison_sociale', "l'intégrateur")} au titre de
l'article 28 du RGPD. **Elle ne remplace pas un contrat de sous-traitance signé**
: elle en documente le contenu attendu, à faire valider par vos conseils
respectifs.

## Qui fait quoi

| | Rôle | Responsabilité |
|---|---|---|
| {e['raison_sociale']} | **Responsable de traitement** | Détermine les finalités, répond aux personnes concernées, choisit les durées |
| {i.get('raison_sociale', "L'intégrateur")} | **Sous-traitant** | Déploie et maintient l'agent sur instruction, n'utilise les données pour aucune finalité propre |

Contact du sous-traitant pour les questions de données : {i.get('contact_donnees', i.get('email', 'à renseigner'))}

## Engagements du sous-traitant

- Ne traiter les données que sur instruction documentée du responsable.
- Garantir la confidentialité des personnes autorisées à y accéder.
- Mettre en œuvre les mesures techniques prévues : signature des webhooks,
  masquage des numéros dans les journaux, purge automatique après
  {c['retention_texte']}, chiffrement des accès.
- Ne pas recruter de sous-traitant ultérieur sans autorisation écrite. Ceux
  déjà mobilisés sont listés dans la politique de confidentialité.
- Assister le responsable dans les demandes d'exercice de droits et les
  notifications de violation, sous 48 heures.
- Restituer ou supprimer les données en fin de prestation, au choix du
  responsable.
- Permettre les audits et fournir les éléments nécessaires à leur réalisation.

## Notification d'incident

Toute violation de données est signalée au responsable **dans les 24 heures**
suivant sa découverte, avec sa nature, les catégories et le volume de données
concernées, et les mesures prises. C'est le responsable qui notifie l'autorité
de contrôle dans les 72 heures.

## Fin de la prestation

À l'échéance, le sous-traitant supprime les données et les copies existantes
dans un délai de 30 jours, sauf obligation légale de conservation, et en atteste
par écrit.
{_pied(c)}"""


DOCUMENTS = {
    "confidentialite": ("Politique de confidentialité", politique_confidentialite),
    "cgu": ("Conditions d'utilisation", conditions_utilisation),
    "mentions": ("Mentions légales", mentions_legales),
    "suppression": ("Suppression de vos données", suppression_donnees),
    "ia": ("Usage de l'intelligence artificielle", transparence_ia),
    "sous-traitance": ("Annexe sous-traitance", annexe_sous_traitance),
}


def documents_disponibles(c: dict) -> dict:
    """L'annexe de sous-traitance n'a de sens qu'en mode agence."""
    dispo = dict(DOCUMENTS)
    if (c.get("mode") or "direct") != "agence":
        dispo.pop("sous-traitance", None)
    return dispo


# ── Rendu HTML ───────────────────────────────────────────────────────────
#
# Un convertisseur Markdown complet serait une dépendance de plus pour six
# documents que nous écrivons nous-mêmes. On ne gère donc que le sous-ensemble
# réellement produit par les gabarits ci-dessus — et rien d'autre, pour que
# l'ajout d'une syntaxe non gérée se voie immédiatement à la relecture.

import html as _html
import re as _re

_STYLE = """
:root{color-scheme:light dark;--fond:#fbfbfa;--texte:#1c1b19;--doux:#5c5852;
--trait:#e0ddd6;--accent:#7a5c2e;--alerte-fond:#fdf3e3;--alerte-trait:#c99a3a}
@media(prefers-color-scheme:dark){:root{--fond:#16151a;--texte:#e9e7e2;
--doux:#a49f96;--trait:#2f2d2a;--accent:#d3ab68;--alerte-fond:#2a2317;
--alerte-trait:#8a6a26}}
*{box-sizing:border-box}
body{margin:0;background:var(--fond);color:var(--texte);
font:16px/1.65 ui-serif,Georgia,"Times New Roman",serif;
-webkit-text-size-adjust:100%}
main{max-width:44rem;margin:0 auto;padding:3rem 1.5rem 5rem}
h1{font-size:1.9rem;line-height:1.2;margin:0 0 2rem;text-wrap:balance}
h2{font-size:1.2rem;margin:2.6rem 0 .8rem;text-wrap:balance;
border-top:1px solid var(--trait);padding-top:1.6rem}
p,li{color:var(--texte)}
a{color:var(--accent)}
ul{padding-left:1.2rem}
li{margin:.35rem 0}
blockquote{margin:0 0 2rem;padding:1rem 1.2rem;background:var(--alerte-fond);
border-left:4px solid var(--alerte-trait);border-radius:2px}
blockquote p{margin:.3rem 0}
.enveloppe{overflow-x:auto;margin:1.2rem 0}
table{border-collapse:collapse;width:100%;font-size:.92rem;
font-family:ui-sans-serif,system-ui,sans-serif}
th,td{text-align:left;padding:.55rem .7rem;border-bottom:1px solid var(--trait);
vertical-align:top}
th{font-weight:600;color:var(--doux);font-size:.8rem;text-transform:uppercase;
letter-spacing:.04em}
hr{border:0;border-top:1px solid var(--trait);margin:2.5rem 0}
em{color:var(--doux)}
nav{font-family:ui-sans-serif,system-ui,sans-serif;font-size:.88rem;
margin-bottom:2.5rem;padding-bottom:1.2rem;border-bottom:1px solid var(--trait)}
nav a{margin-right:1rem;display:inline-block;margin-bottom:.3rem}
"""


def _en_ligne(t: str) -> str:
    t = _html.escape(t)
    t = _re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', t)
    t = _re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = _re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", t)
    return t


def _md_vers_html(md: str) -> str:
    sortie: list[str] = []
    tampon_liste: list[str] = []
    tampon_table: list[str] = []
    tampon_cite: list[str] = []

    def vider():
        if tampon_liste:
            sortie.append("<ul>" + "".join(f"<li>{_en_ligne(x)}</li>" for x in tampon_liste) + "</ul>")
            tampon_liste.clear()
        if tampon_table:
            lignes = [l for l in tampon_table if not _re.match(r"^\|[\s:|-]+\|$", l)]
            cellules = [[c.strip() for c in l.strip("|").split("|")] for l in lignes]
            if cellules:
                tete = "".join(f"<th>{_en_ligne(c)}</th>" for c in cellules[0])
                corps = "".join(
                    "<tr>" + "".join(f"<td>{_en_ligne(c)}</td>" for c in r) + "</tr>"
                    for r in cellules[1:]
                )
                sortie.append(f'<div class="enveloppe"><table><tr>{tete}</tr>{corps}</table></div>')
            tampon_table.clear()
        if tampon_cite:
            sortie.append("<blockquote>" + "".join(f"<p>{_en_ligne(x)}</p>" for x in tampon_cite) + "</blockquote>")
            tampon_cite.clear()

    paragraphe: list[str] = []

    def vider_paragraphe():
        if paragraphe:
            sortie.append(f"<p>{_en_ligne(' '.join(paragraphe))}</p>")
            paragraphe.clear()

    for ligne in md.splitlines():
        nue = ligne.rstrip()
        if nue.startswith("> "):
            vider_paragraphe()
            if tampon_liste or tampon_table:
                vider()
            tampon_cite.append(nue[2:])
            continue
        if nue.startswith("|"):
            vider_paragraphe()
            if tampon_cite or tampon_liste:
                vider()
            tampon_table.append(nue)
            continue
        if nue.startswith("- "):
            vider_paragraphe()
            if tampon_cite or tampon_table:
                vider()
            tampon_liste.append(nue[2:])
            continue
        vider_paragraphe()
        vider()
        if not nue:
            continue
        if nue.startswith("## "):
            sortie.append(f"<h2>{_en_ligne(nue[3:])}</h2>")
        elif nue.startswith("# "):
            sortie.append(f"<h1>{_en_ligne(nue[2:])}</h1>")
        elif nue == "---":
            sortie.append("<hr>")
        else:
            paragraphe.append(nue)

    vider_paragraphe()
    vider()
    return "\n".join(sortie)


def page_html(titre: str, markdown: str, c: dict) -> str:
    liens = "".join(
        f'<a href="/legal/{cle}">{t}</a>'
        for cle, (t, _) in documents_disponibles(c).items()
    )
    return (
        "<!doctype html><html lang=\"fr\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<meta name=\"robots\" content=\"noindex\">"
        f"<title>{_html.escape(titre)}</title><style>{_STYLE}</style></head>"
        f"<body><main><nav>{liens}</nav>{_md_vers_html(markdown)}</main></body></html>"
    )


# ── Vérification ─────────────────────────────────────────────────────────

# Champs sans lesquels un document est faux plutôt qu'incomplet : une mention
# légale sans raison sociale ni adresse ne remplit aucune obligation.
OBLIGATOIRES = (
    ("entreprise", "raison_sociale"),
    ("entreprise", "adresse"),
    ("entreprise", "email"),
    ("entreprise", "representant_legal"),
    ("protection_donnees", "email"),
    ("juridiction", "pays"),
    ("juridiction", "autorite_controle"),
    ("publication", "url_publique"),
)

# Valeurs du fichier d'exemple : recopiées telles quelles, elles produisent des
# documents d'apparence sérieuse au nom d'une entreprise qui n'existe pas.
TEMOINS_EXEMPLE = (
    "Maison Lorette", "exemple.fr", "000 000 000", "01 99 00 00 00",
    "Camille Lorette", "14 rue Bossuet",
)


def verifier(conf: dict) -> list[str]:
    """
    Retourne la liste des problèmes, vide si tout va bien.

    Pensé pour l'assistant d'installation : il génère, puis il vérifie, plutôt
    que d'annoncer que c'est fait. Un document juridique incomplet ne lève
    aucune erreur à l'exécution — il se découvre en contrôle.
    """
    problemes: list[str] = []

    for section, champ in OBLIGATOIRES:
        valeur = str((conf.get(section) or {}).get(champ, "") or "").strip()
        if not valeur:
            problemes.append(f"{section}.{champ} est vide (obligatoire)")

    plat = yaml.safe_dump(conf, allow_unicode=True)
    for temoin in TEMOINS_EXEMPLE:
        if temoin in plat:
            problemes.append(
                f"« {temoin} » vient du fichier d'exemple : remplacez-le par la "
                "valeur réelle de l'entreprise"
            )

    pays = str((conf.get("juridiction") or {}).get("pays_code", "") or "").strip().upper()
    if pays and pays not in AUTORITES:
        problemes.append(
            f"juridiction.pays_code « {pays} » inconnu — valeurs connues : "
            + ", ".join(sorted(AUTORITES))
        )
    elif pays:
        attendue = AUTORITES[pays][1]
        declaree = str((conf.get("juridiction") or {}).get("autorite_controle_url", "") or "")
        if declaree and declaree.rstrip("/") != attendue.rstrip("/"):
            problemes.append(
                f"juridiction.autorite_controle_url ne correspond pas à {pays} "
                f"(attendu : {attendue})"
            )

    heb = conf.get("hebergeur") or {}
    if heb.get("nom") and not str(heb.get("adresse", "") or "").strip():
        problemes.append(
            "hebergeur.adresse est vide : la LCEN impose une adresse joignable "
            "pour l'hébergeur dans les mentions légales"
        )

    url = str((conf.get("publication") or {}).get("url_publique", "") or "")
    if url and not url.startswith("https://"):
        problemes.append("publication.url_publique doit être en HTTPS")

    # Cohérence avec la configuration réellement en vigueur.
    if (conf.get("traitement") or {}).get("conservation_jours") is not None:
        problemes.append(
            "traitement.conservation_jours ne doit pas être déclaré ici : il est "
            "lu dans RETENTION_JOURS, sinon les deux se contredisent au premier "
            "changement de configuration"
        )

    return problemes


def _cli() -> int:
    import argparse
    import json

    # Les autres modules chargent le .env à l'import ; en ligne de commande on
    # ne passe par aucun d'eux. Sans cet appel, --connu et --verifier lisent
    # les valeurs PAR DÉFAUT et annoncent « anthropic / 90 jours » alors que le
    # déploiement tourne sur autre chose : l'assistant écrirait une politique
    # de confidentialité qui contredit la configuration réelle.
    from dotenv import load_dotenv

    load_dotenv()

    p = argparse.ArgumentParser(
        prog="python -m agent.juridique",
        description="Vérifie config/juridique.yaml et affiche ce que le kit sait déjà.",
    )
    p.add_argument("--verifier", action="store_true", help="contrôle le fichier")
    p.add_argument("--connu", action="store_true",
                   help="affiche ce que le kit déduit seul (JSON), pour l'assistant")
    p.add_argument("--pays", help="code pays : rappelle l'autorité de contrôle")
    p.add_argument("--chercher", metavar="NOM",
                   help="cherche une entreprise française (nom, SIREN ou SIRET) "
                        "dans l'annuaire public, pour faire CONFIRMER plutôt que saisir")
    a = p.parse_args()

    if a.chercher:
        trouves = chercher_entreprise(a.chercher)
        if not trouves:
            print("Aucun résultat — ou annuaire indisponible.")
            print("Posez les questions directement (AGENTS.md, étape 2 ter).")
            return 1
        for i, e in enumerate(trouves, 1):
            ferme = "  ⚠️ ÉTABLISSEMENT FERMÉ" if e["etat"] and e["etat"] != "A" else ""
            print(f"\n[{i}] {e['raison_sociale']}{ferme}")
            print(f"    forme_juridique   : {e['forme_juridique']}")
            print(f"    immatriculation   : SIREN {e['siren']} — SIRET {e['siret']}")
            print(f"    adresse           : {e['adresse']}")
            for r in e["representants"]:
                print(f"    representant      : {r}")
        print("\nCes données sont publiques et parfois en retard sur la réalité.")
        print("FAITES CONFIRMER la ligne retenue avant de l'écrire — surtout le")
        print("représentant légal, qui change sans que l'annuaire suive toujours.")
        return 0

    if a.pays:
        code = a.pays.strip().upper()
        if code not in AUTORITES:
            print(f"Pays inconnu. Connus : {', '.join(sorted(AUTORITES))}")
            return 2
        nom, url, loi = AUTORITES[code]
        print(f"autorite_controle:     {nom}")
        print(f"autorite_controle_url: {url}")
        print(f"cadre applicable:      {loi}")
        if code in ZONES_A_REFONDRE:
            print(f"\n⚠️  {ZONES_A_REFONDRE[code]}")
        return 0

    conf = charger()
    if conf is None:
        print("config/juridique.yaml absent. Copiez config/juridique.exemple.yaml.")
        return 1

    if a.connu:
        c = contexte(conf)
        print(json.dumps({
            "retention_jours": c["retention_jours"],
            "fournisseur_ia": c["fournisseur_ia"],
            "mode_transparence": c["mode_transparence"],
            "journalise_contenu": c["journalise_contenu"],
            "sous_traitants": [list(t) for t in c["sous_traitants"]],
        }, ensure_ascii=False, indent=2))
        return 0

    problemes = verifier(conf)
    if not problemes:
        print("✅ config/juridique.yaml est complet et cohérent.")
        if not (conf.get("revue_juridique") or {}).get("effectuee"):
            print("\n⚠️  revue_juridique.effectuee vaut false : les pages publiées")
            print("    portent un bandeau d'avertissement. C'est voulu tant qu'un")
            print("    juriste n'a pas relu.")
        return 0

    print(f"❌ {len(problemes)} problème(s) :\n")
    for x in problemes:
        print(f"  - {x}")
    return 1



# ── Recherche d'entreprise (France) ──────────────────────────────────────
#
# Raison sociale, SIREN, forme juridique, adresse et dirigeants sont des
# données PUBLIQUES en France. Les faire saisir de mémoire, c'est se garantir
# un SIRET faux dans des mentions légales — une erreur qu'aucun test ne
# rattrape et qui ne se voit qu'en contrôle.
#
# Outil d'INSTALLATION, jamais appelé par l'agent en service : le runtime ne
# gagne aucune dépendance réseau.

RECHERCHE_API = "https://recherche-entreprises.api.gouv.fr/search"

# Catégories juridiques INSEE les plus courantes. Un code absent est restitué
# tel quel avec une mention « à confirmer » : mieux vaut avouer qu'on ne sait
# pas que d'écrire « SARL » sur une SAS.
FORMES_INSEE = {
    "1000": "Entrepreneur individuel",
    "5202": "Société en nom collectif (SNC)",
    "5498": "SARL à associé unique (EURL)",
    "5499": "Société à responsabilité limitée (SARL)",
    "5599": "Société anonyme à conseil d'administration (SA)",
    "5699": "Société anonyme à directoire (SA)",
    "5710": "Société par actions simplifiée (SAS)",
    "5720": "Société par actions simplifiée à associé unique (SASU)",
    "6540": "Société civile immobilière (SCI)",
    "9220": "Association déclarée",
}


def forme_juridique(code: str | None) -> str:
    code = str(code or "").strip()
    if not code:
        return ""
    return FORMES_INSEE.get(code, f"catégorie juridique INSEE {code} — à confirmer")


def chercher_entreprise(requete: str, limite: int = 5) -> list[dict]:
    """
    Interroge l'annuaire public des entreprises (data.gouv.fr, sans clé).

    Ne lève jamais : une recherche indisponible ne doit pas bloquer une
    installation. L'assistant retombe alors sur les questions directes.
    """
    import json
    import urllib.parse
    import urllib.request

    url = f"{RECHERCHE_API}?" + urllib.parse.urlencode(
        {"q": requete, "per_page": max(1, min(limite, 10))}
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            donnees = json.load(r)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Recherche d'entreprise indisponible ({e}) ; posez les questions.")
        return []

    resultats = []
    for item in donnees.get("results", []):
        siege = item.get("siege") or {}
        dirigeants = [
            f"{(d.get('prenoms') or '').title()} {(d.get('nom') or '').title()}".strip()
            + (f", {d.get('qualite')}" if d.get("qualite") else "")
            for d in (item.get("dirigeants") or [])
            if d.get("nom")
        ]
        resultats.append({
            "raison_sociale": item.get("nom_complet") or "",
            "siren": item.get("siren") or "",
            "siret": siege.get("siret") or "",
            "forme_juridique": forme_juridique(item.get("nature_juridique")),
            "adresse": (siege.get("adresse") or "").strip(),
            "representants": dirigeants,
            "etat": item.get("etat_administratif") or "",
        })
    return resultats

if __name__ == "__main__":
    raise SystemExit(_cli())
