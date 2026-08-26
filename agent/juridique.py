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
    assume = revue.get("publication_assumee") or {}
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
        # Troisième état : publier sans relecture, en connaissance de cause.
        # Le bandeau disparaît des pages publiques — un avertissement affiché
        # aux clients d'un plombier les alarme sans les protéger, et signale
        # une faiblesse à qui la cherche. Mais la décision reste datée et
        # nommée dans la configuration, et rappelée au démarrage.
        "risque_assume": bool(assume.get("acceptee")),
        "assume_par": assume.get("par") or "",
        "assume_date": assume.get("date") or "",
        "genere_le": date.today().isoformat(),
    }


def _avertissement_console(c: dict) -> dict | None:
    """
    L'avertissement de relecture juridique, à l'usage de la CONSOLE seule.

    Il ne figure plus sur les pages publiques. Un bandeau « non relu par un
    professionnel du droit » sur les CGU d'un artisan inquiète ses clients sans
    les protéger, et signale une faiblesse à qui la cherche — alors que le
    destinataire utile de cet avertissement est l'exploitant, pas le client.

    Retourne None quand une décision a été prise, dans un sens ou dans l'autre.
    """
    if c["revue_faite"] or c["risque_assume"]:
        return None
    return {
        "titre": "Documents non relus par un professionnel du droit",
        "texte": (
            "Ces six pages sont publiées en l'état, à partir de gabarits. Elles "
            "couvrent ce que Meta exige et ce que le RGPD impose dans le cas "
            "courant, mais n'ont pas été écrites pour votre activité ni vos "
            "contrats. Faites-les relire, ou assumez explicitement la "
            "publication ci-dessous — dans les deux cas, la décision est datée "
            "et nommée."
        ),
    }



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

## Qui traite vos données

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

Certaines données peuvent être conservées au-delà de cette durée lorsqu'une
obligation légale l'impose, ou le temps nécessaire à la constatation ou à la
défense d'un droit en justice. Elles sont alors archivées et leur accès est
restreint à ce seul usage.

## Ce qui est obligatoire, et ce qui ne l'est pas

Aucune information ne vous est réclamée pour utiliser le service. Celles que
vous transmettez — nom, adresse d'intervention, description d'un problème —
nous sont nécessaires pour traiter votre demande : sans elles, nous ne pouvons
pas y donner suite, mais vous restez libre de ne pas les fournir et de nous
joindre autrement.

Nous ne collectons aucune donnée à votre sujet auprès de tiers.

## Traceurs

Les pages de ce service ne déposent **aucun cookie** et n'utilisent aucun
traceur : il n'y a donc rien à accepter ni à refuser.

## Vos droits

Vous disposez d'un droit d'**accès**, de **rectification**, d'**effacement**,
de **limitation** du traitement, d'**opposition** pour motif légitime, et de
**portabilité** de vos données. Vous pouvez également définir des **directives
relatives au sort de vos données après votre décès** (article 85 de la loi
Informatique et Libertés).

**Pour exercer ces droits, écrivez à {pd['email']}.** Nous répondons sous un
mois, délai qui peut être prolongé de deux mois pour une demande complexe ;
nous vous en informerions alors. Une preuve d'identité peut être demandée en
cas de doute raisonnable sur l'auteur de la demande. La marche à suivre pour
obtenir l'effacement est détaillée sur notre page
[Suppression de vos données]({(c.get('publication') or {}).get('url_publique', '')}/legal/suppression).

Vous pouvez également écrire « supprimez mes données » directement dans la
conversation WhatsApp.

Si notre réponse ne vous satisfait pas, vous pouvez saisir la {j['autorite_controle']} :
{j.get('autorite_controle_url', '')}

## En cas de violation de données

Si une violation de vos données survenait et présentait un risque élevé pour
vos droits, nous vous en informerions dans les meilleurs délais, conformément
à l'article 34 du RGPD. L'autorité de contrôle serait notifiée sous 72 heures
comme le prévoit l'article 33.

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

Vous pouvez à tout moment demander la suppression des données que
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
    """
    Conditions du SERVICE DE MESSAGERIE, et de rien d'autre.

    L'agent ne vend rien et ne conclut aucun contrat : les clauses de commerce
    électronique — rétractation, prix, paiement, livraison — n'ont pas leur
    place ici. Les importer d'un gabarit de CGV donnerait un document plus
    long et moins solide, qui promettrait d'encadrer ce qu'il n'encadre pas.

    Les conditions commerciales de l'entreprise sont un document distinct, qui
    ne relève pas de ce kit.
    """
    e = c["entreprise"]
    j = c["juridiction"]
    base = (c.get("publication") or {}).get("url_publique", "").rstrip("/")
    tel = e.get("telephone", "")
    return f"""# Conditions d'utilisation du service de messagerie

En vigueur au {c.get('genere_le', '')}.

## Article 1 — Objet et champ d'application

1.1. Les présentes conditions régissent l'usage de l'assistant conversationnel
que {e['raison_sociale']} met à disposition sur WhatsApp, ci-après « le
service ».

1.2. Elles ne régissent **que** ce canal de communication. Les conditions
applicables aux prestations de {e['raison_sociale']}, à leur prix et à leur
exécution font l'objet de documents distincts, remis par l'entreprise.

1.3. En adressant un message à notre numéro, vous acceptez les présentes
conditions. Si vous les refusez, n'utilisez pas le service : nos autres moyens
de contact restent à votre disposition{f", notamment le {tel}" if tel else ""}.

## Article 2 — Description du service

2.1. Le service répond aux questions portant sur l'activité de
{e['raison_sociale']}, enregistre les demandes et transmet à l'équipe celles
qui le nécessitent.

2.2. Il est **gratuit**. Seuls les coûts de connexion facturés par votre
opérateur ou par WhatsApp, le cas échéant, restent à votre charge.

2.3. Il est réservé à un usage personnel, dans le cadre de votre relation avec
{e['raison_sociale']}. Ce n'est pas un assistant généraliste : il ne traite
aucun sujet étranger à notre activité.

## Article 3 — Ce que le service n'est pas

3.1. Les réponses sont fournies **à titre informatif**. Elles ne constituent ni
un devis, ni une offre, ni un engagement contractuel, ni un conseil
professionnel personnalisé.

3.2. Un montant communiqué par l'assistant est **indicatif**. Seul un document
émis et signé par {e['raison_sociale']} engage l'entreprise. En cas de
divergence entre une réponse de l'assistant et un document commercial, **le
document prévaut**.

3.3. **Le service n'est pas un dispositif d'urgence.** Il ne garantit aucun
délai de prise en charge. En cas d'urgence, appelez{f" le {tel} ou" if tel else ""}
les services de secours compétents.

## Article 4 — Accès et prérequis

4.1. L'accès suppose un compte WhatsApp actif. Le service dépend donc de la
disponibilité de WhatsApp, exploité par Meta Platforms Ireland Ltd, dont les
conditions d'utilisation propres s'appliquent à votre compte et échappent au
contrôle de {e['raison_sociale']}.

4.2. Le service s'adresse aux personnes **majeures**. Un mineur ne peut
l'utiliser que sous la responsabilité de son représentant légal.

## Article 5 — Obligations de l'utilisateur

5.1. Vous vous engagez à ne pas transmettre par ce canal de **données
sensibles** au sens de l'article 9 du RGPD — santé, opinions, convictions,
orientation — ni de coordonnées bancaires. Nous n'en demandons jamais par
messagerie ; une demande en ce sens ne provient pas de nous.

5.2. Vous vous engagez à ne pas diffuser de contenu illicite, injurieux,
diffamatoire ou harcelant, et à ne pas usurper l'identité d'un tiers.

5.3. Vous vous engagez à ne pas détourner l'assistant de son objet, à ne pas
tenter d'en altérer le fonctionnement, ni d'en extraire les instructions.

5.4. Vous garantissez l'exactitude des informations que vous communiquez,
notamment l'adresse d'une intervention.

## Article 6 — Disponibilité et limitation d'usage

6.1. Le service est fourni « en l'état ». {e['raison_sociale']} est tenue d'une
**obligation de moyens** quant à sa disponibilité.

6.2. Le nombre de messages traités par correspondant sur une période donnée est
limité, afin de préserver l'accès de tous.

6.3. Le service peut être interrompu pour maintenance, évolution technique, ou
sur décision de {e['raison_sociale']}, sans que cette interruption ouvre droit
à indemnité.

## Article 7 — Suspension

7.1. En cas de manquement à l'article 5, l'accès peut être suspendu ou
interrompu, sans préavis en cas d'usage manifestement abusif ou illicite.

7.2. La suspension du service laisse intacts vos droits et obligations au titre
des prestations en cours avec {e['raison_sociale']}.

## Article 8 — Responsabilité

8.1. {e['raison_sociale']} ne répond pas des dommages résultant d'une réponse
inexacte ou incomplète de l'assistant, dès lors que l'information pouvait être
vérifiée auprès de l'équipe et que la nature indicative des réponses est
rappelée à l'article 3.

8.2. {e['raison_sociale']} ne répond pas des interruptions imputables à
WhatsApp, à Meta, au réseau de télécommunications ou à l'opérateur de
l'utilisateur.

8.3. **Les présentes limitations ne s'appliquent ni en cas de faute lourde ou
dolosive, ni en cas de dommage corporel, ni dans les cas où la loi les écarte.**
Elles ne privent le consommateur d'aucun droit d'ordre public.

## Article 9 — Force majeure

Aucune des parties ne répond d'un manquement causé par un événement de force
majeure au sens de l'article 1218 du Code civil, notamment une panne
généralisée des réseaux, une interruption durable des services de Meta, ou une
décision d'une autorité publique. L'obligation est suspendue pendant la durée
de l'événement.

## Article 10 — Preuve

10.1. Les échanges intervenus par ce canal sont conservés dans les conditions
décrites par notre [politique de confidentialité]({base}/legal/confidentialite).

10.2. Les parties conviennent que ces enregistrements, ainsi que les journaux
techniques du service, constituent un **mode de preuve recevable** de la teneur
et de la date des échanges, sauf preuve contraire rapportée par tout moyen.

## Article 11 — Propriété intellectuelle

Les contenus diffusés par l'assistant — textes, documents, tarifs — demeurent
la propriété de {e['raison_sociale']}. Ils vous sont communiqués pour votre
information personnelle et ne peuvent être reproduits ou diffusés publiquement
sans autorisation écrite.

## Article 12 — Données personnelles

Le traitement de vos données est décrit dans notre
[politique de confidentialité]({base}/legal/confidentialite). La procédure
d'effacement figure sur la page
[Suppression de vos données]({base}/legal/suppression). L'usage de
l'intelligence artificielle est détaillé sur la page
[Usage de l'intelligence artificielle]({base}/legal/ia).

## Article 13 — Durée et modification

13.1. Les présentes s'appliquent tant que vous utilisez le service. Vous pouvez
cesser de l'utiliser à tout moment, sans formalité.

13.2. Elles peuvent être modifiées. La version applicable est celle publiée à
cette adresse à la date de votre message ; sa date d'entrée en vigueur figure
en tête du document. Une modification substantielle est signalée dans la
conversation.

## Article 14 — Réclamation, droit applicable et juridiction

14.1. Toute réclamation relative au service peut être adressée à
{e['email']}. Nous accusons réception sous 5 jours ouvrés et répondons dans un
délai raisonnable.

14.2. Si l'une des présentes clauses était déclarée nulle ou inapplicable, les
autres conserveraient leur plein effet.

14.3. Les présentes sont soumises au {j['droit_applicable']}. À défaut de
résolution amiable, compétence est attribuée aux {j['tribunal']}, sous réserve
des règles impératives protégeant le consommateur.
{_pied(c)}"""



def mentions_legales(c: dict) -> str:
    """
    Identité de l'éditeur, telle qu'exigée par la LCEN.

    Les champs facultatifs — TVA, assurance, RM — n'apparaissent que s'ils sont
    renseignés : une ligne « non précisé » dans des mentions légales est pire
    qu'une absence, elle signale un document rempli sans être relu.
    """
    e = c["entreprise"]
    h = c.get("hebergeur") or {}
    base = (c.get("publication") or {}).get("url_publique", "").rstrip("/")

    lignes = [e["raison_sociale"]]
    for cle in ("forme_juridique", "immatriculation", "adresse"):
        if e.get(cle):
            lignes.append(str(e[cle]))
    if e.get("telephone"):
        lignes.append(f"Téléphone : {e['telephone']}")
    lignes.append(f"Courriel : {e['email']}")
    if e.get("tva_intracommunautaire"):
        lignes.append(f"TVA intracommunautaire : {e['tva_intracommunautaire']}")
    identite = "  \n".join(lignes)

    # L'assurance figure dans la configuration depuis l'origine sans avoir
    # jamais été rendue. Pour un artisan du bâtiment, elle est attendue :
    # la loi du 18 juin 2014 impose d'indiquer assureur et couverture sur les
    # devis et factures, et l'omettre ici détonne avec ces documents.
    assurance = ""
    if e.get("assurance"):
        assurance = f"""

## Assurance professionnelle

{e['assurance']}"""

    heb = [h.get("nom", "non précisé")]
    if h.get("adresse"):
        heb.append(str(h["adresse"]))
    if h.get("telephone"):
        heb.append(str(h["telephone"]))
    hebergeur = "  \n".join(heb)

    return f"""# Mentions légales

## Éditeur du service

{identite}

Directeur de la publication : {e['representant_legal']}{assurance}

## Hébergeur

{hebergeur}

Les données du service sont hébergées en **{h.get('pays_donnees', 'non précisé')}**.

## Nature du service

Ce service est un canal de communication mis à disposition par
{e['raison_sociale']}. Il ne constitue pas une plateforme de vente en ligne :
aucune commande n'y est conclue et aucun paiement n'y est traité. Les
conditions d'usage figurent sur la page
[Conditions d'utilisation]({base}/legal/cgu).

## Propriété intellectuelle

Les contenus diffusés par l'assistant — textes, documents, tarifs — demeurent
la propriété de {e['raison_sociale']}, de même que la marque et les signes
distinctifs qui y figurent. Toute reproduction ou diffusion publique sans
autorisation écrite est interdite.

## Données personnelles

Le traitement des données est décrit sur la page
[Politique de confidentialité]({base}/legal/confidentialite).
Contact : {(c.get('protection_donnees') or {}).get('email', e['email'])}.

## Signalement d'un contenu illicite

Pour signaler un contenu illicite diffusé par ce service, écrivez à
{e['email']} en précisant la date, le contenu concerné et le motif du
signalement. Nous accusons réception sous 5 jours ouvrés.
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

## Vous parlez à une machine, et vous en êtes informé

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

Cette annexe précise la répartition des rôles entre
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
        # Les lignes ordinaires s'ACCUMULENT : un paragraphe markdown court sur
        # plusieurs lignes. Vider avant chaque ligne produisait un <p> par ligne
        # source, et coupait tout ce qui s'étend au-delà — un **gras** ou un
        # [lien](…) à cheval sur un retour restait affiché en markdown brut.
        if not nue:
            vider_paragraphe()
            vider()
            continue
        if nue.startswith(("## ", "# ")) or nue == "---":
            vider_paragraphe()
            vider()
            if nue.startswith("## "):
                sortie.append(f"<h2>{_en_ligne(nue[3:])}</h2>")
            elif nue.startswith("# "):
                sortie.append(f"<h1>{_en_ligne(nue[2:])}</h1>")
            else:
                sortie.append("<hr>")
            continue
        vider()
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
        # Pas de noindex : ce sont des documents PUBLICS, et les Platform Terms
        # de Meta exigent une politique « publicly available, easily accessible
        # (including by our crawlers), and non-geoblocked ». Un lien inaccessible
        # est traité comme une violation, pas comme une préférence.
        ""
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
        revue = conf.get("revue_juridique") or {}
        assume = revue.get("publication_assumee") or {}
        if revue.get("effectuee"):
            qui = revue.get("par") or "non précisé"
            print(f"\n📄 Relu par {qui}. Pages publiées sans bandeau.")
        elif assume.get("acceptee"):
            qui = assume.get("par") or "non précisé"
            quand = assume.get("date") or "sans date"
            print(f"\n📄 Publication assumée sans relecture juridique.")
            print(f"    Décidé par {qui}, le {quand}.")
            print("    Les pages sont publiées sans bandeau. La décision reste")
            print("    tracée ici et rappelée à chaque démarrage.")
        else:
            print("\n⚠️  Aucune décision prise : les pages portent un bandeau")
            print("    « non relu par un professionnel du droit ».")
            print("    Deux issues — faire relire (revue_juridique.effectuee),")
            print("    ou assumer explicitement (publication_assumee.acceptee).")
        return 0

    print(f"❌ {len(problemes)} problème(s) :\n")
    for x in problemes:
        print(f"  - {x}")
    return 1




# ── Décision de publication, depuis la console ───────────────────────────

DECISIONS = ("aucune", "assumee", "relue")


def enregistrer_decision(decision: str, par: str, date_iso: str | None = None) -> None:
    """
    Réécrit le SEUL bloc revue_juridique, en laissant le reste intact.

    On ne repasse pas par yaml.safe_dump : il reformaterait tout le fichier et
    perdrait les commentaires — or ce sont eux qui portent les avertissements
    juridiques, c'est-à-dire ce qu'on veut le moins effacer en cochant une case
    dans une interface.
    """
    if decision not in DECISIONS:
        raise ErreurJuridique(f"Décision inconnue : {decision}")
    if decision != "aucune" and not par.strip():
        raise ErreurJuridique("Indiquez qui décide : la décision doit être nommée.")
    if not FICHIER.exists():
        raise ErreurJuridique("config/juridique.yaml est absent.")

    quand = date_iso or date.today().isoformat()
    qui = par.strip().replace('"', "'")

    if decision == "relue":
        neuf = (
            "revue_juridique:\n"
            "  effectuee: true\n"
            f'  par: "{qui}"\n'
            f'  date: "{quand}"\n'
            "  publication_assumee:\n"
            "    acceptee: false\n"
            '    par: ""\n'
            '    date: ""\n'
        )
    elif decision == "assumee":
        neuf = (
            "revue_juridique:\n"
            "  effectuee: false\n"
            '  par: ""\n'
            '  date: ""\n'
            "  publication_assumee:\n"
            "    acceptee: true\n"
            f'    par: "{qui}"\n'
            f'    date: "{quand}"\n'
        )
    else:
        neuf = (
            "revue_juridique:\n"
            "  effectuee: false\n"
            '  par: ""\n'
            '  date: ""\n'
            "  publication_assumee:\n"
            "    acceptee: false\n"
            '    par: ""\n'
            '    date: ""\n'
        )

    texte = FICHIER.read_text(encoding="utf-8")
    lignes = texte.splitlines(keepends=True)
    debut = next(
        (i for i, l in enumerate(lignes) if l.startswith("revue_juridique:")), None
    )
    if debut is None:
        FICHIER.write_text(texte.rstrip("\n") + "\n\n" + neuf, encoding="utf-8")
        return

    # Le bloc court jusqu'à la prochaine clé de premier niveau, commentaires
    # intermédiaires compris : les laisser derrière produirait un fichier
    # commenté pour un réglage qui n'y est plus.
    fin = len(lignes)
    for i in range(debut + 1, len(lignes)):
        l = lignes[i]
        if l.strip() and not l[0].isspace() and not l.lstrip().startswith("#"):
            fin = i
            break
    FICHIER.write_text(
        "".join(lignes[:debut]) + neuf + "".join(lignes[fin:]), encoding="utf-8"
    )


def etat_publication(conf: dict) -> dict:
    """Ce que la console affiche : l'état, et ce qu'il implique."""
    revue = conf.get("revue_juridique") or {}
    assume = revue.get("publication_assumee") or {}
    if revue.get("effectuee"):
        return {
            "decision": "relue", "par": revue.get("par") or "",
            "date": revue.get("date") or "", "bandeau": False,
        }
    if assume.get("acceptee"):
        return {
            "decision": "assumee", "par": assume.get("par") or "",
            "date": assume.get("date") or "", "bandeau": False,
        }
    return {"decision": "aucune", "par": "", "date": "", "bandeau": True}

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
