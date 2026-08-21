"""
Sécurité et conformité RGPD.

Ce module existe parce que trois choses manquaient dans le kit d'origine et
qu'elles ne sont pas optionnelles quand l'agent parle à de vrais clients :

1. Les logs contenaient le numéro de téléphone complet et le texte des messages.
   Hébergés sur Railway ou ailleurs, ce sont des données personnelles chez un
   tiers, sans base légale ni durée de conservation. Ici on masque par défaut.
2. Rien ne limitait le nombre de messages par client : un seul importun pouvait
   faire exploser la facture Claude.
3. Rien ne plafonnait la dépense quotidienne globale.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

logger = logging.getLogger("agentkit")

# ── Masquage des données personnelles ────────────────────────────────────

# Mettre LOG_MESSAGE_CONTENT=true UNIQUEMENT en développement local.
LOGUER_CONTENU = os.getenv("LOG_MESSAGE_CONTENT", "false").strip().lower() == "true"

# Sel de hachage : sans lui, un numéro de téléphone se retrouve par force brute
# en quelques secondes (l'espace des numéros français fait 10^9, c'est trivial).
_SEL = os.getenv("PII_HASH_SALT", "").strip()
if not _SEL:
    _SEL = "agentkit-sel-par-defaut"


def masquer_telephone(telephone: str) -> str:
    """
    Transforme un numéro en identifiant stable et non réversible.

    On garde les 2 derniers chiffres : assez pour suivre une conversation dans
    les logs quand on débogue, pas assez pour réidentifier quelqu'un.

        33612345678  ->  tel_a3f9c1d2…78
    """
    if not telephone:
        return "tel_inconnu"
    empreinte = hashlib.sha256((_SEL + telephone).encode("utf-8")).hexdigest()[:8]
    return f"tel_{empreinte}…{telephone[-2:]}"


def masquer_contenu(texte: str, maximum: int = 60) -> str:
    """
    Le contenu d'un message client ne part pas dans les logs par défaut.

    On journalise seulement de quoi diagnostiquer : la longueur du message.
    """
    if not texte:
        return "<vide>"
    if not LOGUER_CONTENU:
        return f"<{len(texte)} caractères, contenu masqué>"
    texte = texte.replace("\n", " ")
    return texte if len(texte) <= maximum else texte[: maximum - 1] + "…"


# ── Limitation de débit par client ───────────────────────────────────────


@dataclass
class LimiteurDebit:
    """
    Fenêtre glissante, en mémoire, par numéro de téléphone.

    En mémoire = remis à zéro au redéploiement et non partagé entre plusieurs
    instances. C'est assez pour un agent mono-instance (le cas de ce kit) et
    ça évite d'imposer Redis. Si vous passez à plusieurs répliques, il faut
    déplacer ce compteur en base.
    """

    max_messages: int = int(os.getenv("RATE_LIMIT_MESSAGES", "20") or "20")
    fenetre_secondes: int = int(os.getenv("RATE_LIMIT_FENETRE_S", "3600") or "3600")
    _historique: dict[str, deque] = field(default_factory=lambda: defaultdict(deque))

    def autoriser(self, telephone: str) -> tuple[bool, int]:
        """Retourne (autorisé, messages_restants)."""
        maintenant = time.monotonic()
        passages = self._historique[telephone]

        while passages and maintenant - passages[0] > self.fenetre_secondes:
            passages.popleft()

        if len(passages) >= self.max_messages:
            return False, 0

        passages.append(maintenant)
        return True, self.max_messages - len(passages)

    def purger(self) -> int:
        """Oublie les numéros inactifs. Sans ça le dictionnaire grossit sans fin."""
        maintenant = time.monotonic()
        morts = [
            tel
            for tel, passages in self._historique.items()
            if not passages or maintenant - passages[-1] > self.fenetre_secondes * 2
        ]
        for tel in morts:
            del self._historique[tel]
        return len(morts)


# ── Plafond de dépense ───────────────────────────────────────────────────

# Tarifs API en dollars par million de tokens (entrée, sortie).
TARIFS = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


@dataclass
class CompteurDepense:
    """
    Estime la dépense Claude du jour et coupe au-delà du plafond.

    C'est une estimation locale, pas la facturation réelle d'Anthropic : elle
    sert de coupe-circuit, pas de comptabilité. Mise à zéro au redémarrage.
    """

    plafond_journalier: float = float(os.getenv("PLAFOND_DEPENSE_JOUR", "5") or "5")
    _depense: float = 0.0
    _jour: str = ""

    def _verifier_jour(self) -> None:
        aujourdhui = time.strftime("%Y-%m-%d")
        if aujourdhui != self._jour:
            self._jour = aujourdhui
            self._depense = 0.0

    def enregistrer(self, modele: str, tokens_entree: int, tokens_sortie: int) -> float:
        self._verifier_jour()
        prix_e, prix_s = TARIFS.get(modele, (3.0, 15.0))
        cout = (tokens_entree / 1_000_000) * prix_e + (tokens_sortie / 1_000_000) * prix_s
        self._depense += cout
        return cout

    def depassement(self) -> bool:
        self._verifier_jour()
        if self.plafond_journalier <= 0:
            return False
        return self._depense >= self.plafond_journalier

    @property
    def depense_du_jour(self) -> float:
        self._verifier_jour()
        return round(self._depense, 4)


limiteur = LimiteurDebit()
depenses = CompteurDepense()
