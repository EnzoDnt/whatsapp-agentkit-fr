"""Sélection du fournisseur WhatsApp d'après WHATSAPP_PROVIDER."""

from __future__ import annotations

import os

from agent.providers.base import (
    ErreurConfiguration,
    FournisseurWhatsApp,
    MessageEntrant,
)

FOURNISSEURS = ("simulateur", "meta")


def obtenir_fournisseur() -> FournisseurWhatsApp:
    """
    Instancie le fournisseur configuré.

    Volontairement appelé au démarrage et non à l'import : si la configuration
    est mauvaise, le serveur doit démarrer quand même et l'expliquer dans son
    health check, plutôt que de mourir à l'import et laisser l'hébergeur
    redémarrer le conteneur en boucle sans message lisible.
    """
    choix = os.getenv("WHATSAPP_PROVIDER", "simulateur").strip().lower()

    if choix in ("", "simulateur", "simulator", "local"):
        from agent.providers.simulateur import FournisseurSimulateur

        return FournisseurSimulateur()

    if choix == "meta":
        from agent.providers.meta import FournisseurMeta

        return FournisseurMeta()

    raise ErreurConfiguration(
        f"Fournisseur inconnu : '{choix}'. Valeurs acceptées : {' | '.join(FOURNISSEURS)}"
    )


__all__ = [
    "ErreurConfiguration",
    "FournisseurWhatsApp",
    "MessageEntrant",
    "FOURNISSEURS",
    "obtenir_fournisseur",
]
