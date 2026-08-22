"""
Où tourne-t-on, et la configuration est-elle sûre ?

Ce module existe à cause d'un enchaînement observé en audit. Quatre protections
se réglaient sur la seule variable ENVIRONMENT :

  - le secret de signature des sessions,
  - l'attribut Secure du cookie,
  - le montage du simulateur, qui est un canal sans authentification,
  - l'acceptation de webhooks non signés.

Or ENVIRONMENT n'est pas une clé d'API : rien ne s'arrête quand elle manque.
Une personne qui déploie en suivant le guide renseigne ses clés WhatsApp et
Anthropic — sans quoi l'agent ne répond pas, elle le voit tout de suite — et
peut parfaitement oublier ENVIRONMENT sans qu'aucun signal ne l'alerte. Les
quatre protections tombaient alors d'un coup, en silence.

D'où les deux principes appliqués ici :

1. **On devine l'hébergement.** Les plateformes annoncent leur présence par
   leurs propres variables. On ne dépend plus d'une déclaration de l'utilisateur.
2. **Le défaut penche du côté sûr.** Une permission s'accorde explicitement ;
   elle ne se déduit jamais d'une absence.
"""

from __future__ import annotations

import os

# Variables posées par les plateformes elles-mêmes. Leur présence prouve qu'on
# n'est pas sur le portable de quelqu'un, quoi qu'annonce ENVIRONMENT.
INDICES_HEBERGEMENT = (
    "RAILWAY_ENVIRONMENT", "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID",
    "COOLIFY_URL", "COOLIFY_FQDN", "COOLIFY_CONTAINER_NAME", "COOLIFY_RESOURCE_UUID",
    "RENDER", "RENDER_SERVICE_ID",
    "FLY_APP_NAME", "FLY_MACHINE_ID",
    "DYNO", "HEROKU_APP_ID",
    "KUBERNETES_SERVICE_HOST",
    "AWS_EXECUTION_ENV", "WEBSITE_INSTANCE_ID", "K_SERVICE",
)


def _declare() -> str:
    return os.getenv("ENVIRONMENT", "").strip().lower()


def heberge() -> bool:
    """Vrai si l'on tourne sur une plateforme d'hébergement, quoi qu'on déclare."""
    return any(os.getenv(v, "").strip() for v in INDICES_HEBERGEMENT)


def est_production() -> bool:
    """
    Faut-il appliquer le régime de production ?

    Oui si on le déclare, oui aussi si l'on détecte un hébergeur. Le second cas
    rattrape l'oubli, qui est le cas le plus fréquent et le plus coûteux.
    """
    if _declare() == "production":
        return True
    if _declare() == "development":
        # Déclaration explicite : on la respecte, même chez un hébergeur. Cela
        # permet de monter une préproduction volontairement permissive.
        return False
    return heberge()


def est_developpement_declare() -> bool:
    """
    Vrai seulement si l'on a explicitement demandé le mode développement.

    Sert à monter le simulateur, qui est un canal SANS authentification :
    quelqu'un qui connaît l'URL peut injecter des messages et consommer le
    crédit du modèle. Il s'obtient donc sur demande expresse, jamais par
    défaut — c'est l'inverse de la règle précédente (« tout ce qui n'est pas
    production »), qui l'ouvrait au moindre oubli.
    """
    return _declare() == "development"


def audit_configuration() -> list[dict]:
    """
    Passe en revue la configuration et retourne les problèmes trouvés.

    Appelé au démarrage pour l'écrire dans les journaux, et exposé par le point
    de santé : sur un hébergeur, personne ne lit les journaux de démarrage, mais
    tout le monde sait ouvrir l'URL de son agent.
    """
    problemes: list[dict] = []

    def signaler(gravite: str, sujet: str, explication: str, remede: str) -> None:
        problemes.append(
            {"gravite": gravite, "sujet": sujet, "explication": explication, "remede": remede}
        )

    en_ligne = est_production()

    # Le simulateur en ligne : le mode d'échec le plus silencieux du kit.
    # Sa valeur par défaut fait démarrer l'agent sans WhatsApp, avec un statut
    # « ok » trompeur — il n'est branché à rien. En ligne, c'est presque
    # toujours une variable WHATSAPP_PROVIDER=meta oubliée.
    fournisseur = os.getenv("WHATSAPP_PROVIDER", "").strip().lower()
    if en_ligne and fournisseur in ("", "simulateur", "simulator", "local"):
        signaler(
            "critique", "WHATSAPP_PROVIDER",
            "L'agent tourne en mode simulateur : il n'est connecté à AUCUN "
            "WhatsApp et ne recevra jamais de message réel, malgré un statut « ok ».",
            "Ajoutez WHATSAPP_PROVIDER=meta dans vos variables, avec les clés Meta.",
        )

    if en_ligne and not os.getenv("SESSION_SECRET", "").strip():
        signaler(
            "critique", "SESSION_SECRET",
            "Sans lui, les sessions de la console tombent à chaque redéploiement.",
            "openssl rand -hex 32, puis ajoutez SESSION_SECRET dans vos variables.",
        )

    if en_ligne and not os.getenv("PII_HASH_SALT", "").strip():
        signaler(
            "moyenne", "PII_HASH_SALT",
            "Les numéros de téléphone sont masqués dans les journaux avec un sel "
            "public : ils redeviennent retrouvables par force brute.",
            "openssl rand -hex 16, puis ajoutez PII_HASH_SALT.",
        )

    if os.getenv("LOG_MESSAGE_CONTENT", "").strip().lower() == "true" and en_ligne:
        signaler(
            "haute", "LOG_MESSAGE_CONTENT",
            "Le texte des messages clients part en clair dans les journaux de "
            "l'hébergeur : données personnelles chez un tiers, sans durée de "
            "conservation.",
            "Retirez LOG_MESSAGE_CONTENT ou mettez-la à false.",
        )

    if os.getenv("AUTORISER_WEBHOOK_NON_SIGNE", "").strip().lower() == "true" and en_ligne:
        signaler(
            "critique", "AUTORISER_WEBHOOK_NON_SIGNE",
            "Les webhooks ne sont pas vérifiés : n'importe qui connaissant l'URL "
            "peut faire parler votre numéro professionnel et brûler votre crédit.",
            "Retirez cette variable et renseignez META_APP_SECRET.",
        )

    if en_ligne and _declare() != "production":
        signaler(
            "info", "ENVIRONMENT",
            "Hébergeur détecté alors qu'ENVIRONMENT n'est pas à « production » : "
            "le régime de production a été appliqué d'office.",
            "Ajoutez ENVIRONMENT=production pour lever l'ambiguïté.",
        )

    if en_ligne and os.getenv("DATABASE_URL", "").strip().startswith("sqlite"):
        signaler(
            "haute", "DATABASE_URL",
            "SQLite vit dans le conteneur, dont le disque est effacé à chaque "
            "redéploiement : l'historique de toutes les conversations disparaît.",
            "Ajoutez une base PostgreSQL et pointez DATABASE_URL dessus.",
        )

    if os.getenv("TEST_ALLOWLIST", "").strip() and en_ligne:
        signaler(
            "haute", "TEST_ALLOWLIST",
            "Mode test encore actif en ligne : l'agent ne répond QU'aux numéros "
            "listés et ignore tous vos autres clients en silence.",
            "Videz TEST_ALLOWLIST pour ouvrir l'agent à tout le monde.",
        )

    return problemes
