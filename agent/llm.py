"""
Couche d'abstraction des fournisseurs de modèles.

Le kit est pensé pour Claude, mais rien n'oblige à s'y tenir : un intégrateur
peut avoir un compte OpenAI, vouloir router par OpenRouter, ou tester Gemini.

Choix de conception : la boucle d'outils vit DANS l'adaptateur, pas dans
brain.py. Les deux familles d'API ne représentent pas du tout les appels
d'outils de la même façon — blocs `tool_use` contre `tool_calls`, résultats en
message utilisateur contre messages `role: tool`. Faire remonter ça dans le
cerveau imposerait un format pivot que chaque ajout de fournisseur viendrait
tordre. Ici, chaque adaptateur parle sa langue et ne rend qu'un texte.

Quatre fournisseurs, deux implémentations : Anthropic a sa propre API, et
OpenAI, OpenRouter et Google exposent tous les trois une API compatible OpenAI.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Awaitable, Callable

logger = logging.getLogger("agentkit")

# nom → (base_url, variable d'environnement de la clé, modèle par défaut)
COMPATIBLES_OPENAI = {
    # gpt-5.6-terra plutôt que sol : c'est la variante équilibrée, l'équivalent
    # de Sonnet chez Anthropic. Un agent de service client n'a pas besoin du
    # modèle le plus capable, il a besoin du bon rapport qualité-prix.
    "openai": (None, "OPENAI_API_KEY", "gpt-5.6-terra"),
    # Kimi K3 : bon compromis et disponible sans compte chez l'éditeur.
    # N'importe quel modèle du catalogue OpenRouter fonctionne à la place.
    "openrouter": (
        "https://openrouter.ai/api/v1",
        "OPENROUTER_API_KEY",
        "moonshotai/kimi-k3",
    ),
    # Flash et non Pro : le moins cher du lot, largement suffisant pour de la
    # réponse courte adossée à des documents.
    "google": (
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "GOOGLE_API_KEY",
        "gemini-3.7-flash",
    ),
}

FOURNISSEURS = ("anthropic", *COMPATIBLES_OPENAI)

# Tarifs en dollars par million de tokens (entrée, sortie), pour l'estimation
# de dépense. Un modèle absent retombe sur une valeur médiane : le plafond
# reste un garde-fou utile même sans tarif exact.
TARIFS = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "gpt-5.6": (2.0, 12.0),
    "gpt-5.6-sol": (5.0, 30.0),
    "gpt-5.6-terra": (2.0, 12.0),
    "gpt-5.6-luna": (0.50, 4.0),
    "gemini-3.7-flash": (0.375, 1.875),
    "kimi-k3": (3.0, 15.0),
}
TARIF_PAR_DEFAUT = (3.0, 15.0)


def tarif(modele: str) -> tuple[float, float]:
    if modele in TARIFS:
        return TARIFS[modele]
    # OpenRouter préfixe par l'éditeur : « anthropic/claude-sonnet-5 »
    court = modele.split("/")[-1]
    return TARIFS.get(court, TARIF_PAR_DEFAUT)


class ErreurLLM(RuntimeError):
    """Configuration de modèle inutilisable : on le dit clairement, tôt."""


@dataclass
class Reponse:
    texte: str = ""
    tokens_entree: int = 0
    tokens_sortie: int = 0
    tours: int = 1
    tronquee: bool = False
    outils_appeles: list[str] = field(default_factory=list)


# Signature de la fonction qui exécute réellement un outil.
Executeur = Callable[[str, dict], Awaitable[str]]


class ClientLLM:
    """Contrat commun. `converser` gère la boucle d'outils de bout en bout."""

    nom = "base"

    async def converser(
        self,
        systeme: str,
        historique: list[dict],
        message: str,
        outils: list[dict],
        executer: Executeur,
        max_tours: int = 5,
    ) -> Reponse:
        raise NotImplementedError


# ── Anthropic ────────────────────────────────────────────────────────────


class ClientAnthropic(ClientLLM):
    nom = "anthropic"

    def __init__(self, modele: str, max_tokens: int, effort: str) -> None:
        from anthropic import AsyncAnthropic

        cle = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not cle:
            raise ErreurLLM(
                "ANTHROPIC_API_KEY est vide. Obtenez une clé sur "
                "https://platform.anthropic.com/settings/keys"
            )
        self.client = AsyncAnthropic(api_key=cle)
        self.modele = modele
        self.max_tokens = max_tokens
        self.effort = effort
        self._supporte_effort = True

    def _erreur_due_a_effort(self, e: Exception) -> bool:
        """Vrai seulement si l'appel a été refusé À CAUSE de output_config."""
        if getattr(e, "status_code", None) != 400:
            return False
        t = str(e).lower()
        return "output_config" in t or "effort" in t

    @staticmethod
    def _texte(reponse) -> str:
        """
        Les modèles qui raisonnent renvoient d'abord un bloc de réflexion :
        prendre content[0].text donnerait une chaîne vide ou une exception.
        """
        parts = [b.text for b in reponse.content if getattr(b, "type", None) == "text"]
        return "\n".join(p for p in parts if p).strip()

    async def converser(self, systeme, historique, message, outils, executer, max_tours=5):
        messages = [{"role": m["role"], "content": m["content"]} for m in historique]
        messages.append({"role": "user", "content": message})
        bilan = Reponse()

        for tour in range(max_tours):
            extra = (
                {"output_config": {"effort": self.effort}}
                if (self._supporte_effort and self.effort)
                else {}
            )
            try:
                r = await self.client.messages.create(
                    model=self.modele, max_tokens=self.max_tokens, system=systeme,
                    messages=messages, tools=outils, **extra,
                )
            except Exception as e:  # noqa: BLE001
                if extra and self._erreur_due_a_effort(e):
                    logger.warning(f"{self.modele} refuse output_config.effort ; réessai sans.")
                    self._supporte_effort = False
                    r = await self.client.messages.create(
                        model=self.modele, max_tokens=self.max_tokens, system=systeme,
                        messages=messages, tools=outils,
                    )
                else:
                    raise

            bilan.tokens_entree += r.usage.input_tokens
            bilan.tokens_sortie += r.usage.output_tokens
            bilan.tours = tour + 1

            if r.stop_reason != "tool_use":
                bilan.tronquee = r.stop_reason == "max_tokens"
                bilan.texte = self._texte(r)
                return bilan

            # Tous les tool_result d'un même tour partent dans UN message
            # utilisateur : les séparer apprend au modèle à ne plus paralléliser.
            messages.append({"role": "assistant", "content": r.content})
            resultats = []
            for bloc in r.content:
                if getattr(bloc, "type", None) != "tool_use":
                    continue
                bilan.outils_appeles.append(bloc.name)
                resultats.append({
                    "type": "tool_result",
                    "tool_use_id": bloc.id,
                    "content": await executer(bloc.name, dict(bloc.input or {})),
                })
            messages.append({"role": "user", "content": resultats})

        return bilan


# ── OpenAI, OpenRouter, Google ───────────────────────────────────────────


class ClientCompatibleOpenAI(ClientLLM):
    """
    Couvre OpenAI, OpenRouter et Google, qui exposent tous la même API.

    Différences avec Anthropic à connaître : le prompt système est le premier
    message, les schémas d'outils sont enveloppés dans un objet `function`, et
    chaque résultat d'outil est un message distinct portant `role: "tool"`.
    """

    def __init__(self, fournisseur: str, modele: str, max_tokens: int) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as e:  # pragma: no cover
            raise ErreurLLM(
                f"Le fournisseur « {fournisseur} » a besoin du paquet openai.\n"
                "  Il est normalement dans requirements.txt ; si vous l'avez retiré,\n"
                "  remettez-le puis réinstallez :  uv pip install openai"
            ) from e

        base, variable, _ = COMPATIBLES_OPENAI[fournisseur]
        cle = os.getenv(variable, "").strip()
        if not cle:
            raise ErreurLLM(f"{variable} est vide : impossible d'utiliser « {fournisseur} ».")

        self.nom = fournisseur
        self.client = AsyncOpenAI(api_key=cle, base_url=base)
        self.modele = modele
        self.max_tokens = max_tokens
        # Les modeles a raisonnement d'OpenAI (gpt-5.6 et suivants) appliquent
        # un reasoning_effort par defaut que /v1/chat/completions refuse des
        # qu'on joint des outils. L'agent en utilise a chaque tour : sans
        # reasoning_effort="none", tout message part en erreur 400.
        # Les modeles qui ignorent le parametre le rejettent : on retombe alors
        # sur un appel nu, une seule fois.
        self._supporte_effort_none = True

    @staticmethod
    def _outils(outils: list[dict]) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": o["name"],
                    "description": o["description"],
                    "parameters": o["input_schema"],
                },
            }
            for o in outils
        ]

    async def converser(self, systeme, historique, message, outils, executer, max_tours=5):
        messages = [{"role": "system", "content": systeme}]
        messages += [{"role": m["role"], "content": m["content"]} for m in historique]
        messages.append({"role": "user", "content": message})
        bilan = Reponse()

        for tour in range(max_tours):
            extra = {"reasoning_effort": "none"} if self._supporte_effort_none else {}
            try:
                r = await self.client.chat.completions.create(
                    model=self.modele,
                    messages=messages,
                    tools=self._outils(outils),
                    max_completion_tokens=self.max_tokens,
                    **extra,
                )
            except Exception as e:  # noqa: BLE001
                if not extra or "reasoning_effort" not in str(e):
                    raise
                logger.warning(
                    f"{self.modele} refuse reasoning_effort ; reessai sans."
                )
                self._supporte_effort_none = False
                r = await self.client.chat.completions.create(
                    model=self.modele,
                    messages=messages,
                    tools=self._outils(outils),
                    max_completion_tokens=self.max_tokens,
                )
            if r.usage:
                bilan.tokens_entree += r.usage.prompt_tokens or 0
                bilan.tokens_sortie += r.usage.completion_tokens or 0
            bilan.tours = tour + 1

            choix = r.choices[0]
            appels = choix.message.tool_calls or []

            if not appels:
                bilan.tronquee = choix.finish_reason == "length"
                bilan.texte = (choix.message.content or "").strip()
                return bilan

            messages.append(choix.message.model_dump(exclude_none=True))
            for appel in appels:
                import json

                bilan.outils_appeles.append(appel.function.name)
                try:
                    arguments = json.loads(appel.function.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                messages.append({
                    "role": "tool",
                    "tool_call_id": appel.id,
                    "content": await executer(appel.function.name, arguments),
                })

        return bilan


# ── Fabrique ─────────────────────────────────────────────────────────────


def modele_par_defaut(fournisseur: str) -> str:
    if fournisseur == "anthropic":
        return "claude-sonnet-5"
    return COMPATIBLES_OPENAI[fournisseur][2]


def obtenir_client(
    fournisseur: str | None = None,
    modele: str | None = None,
    max_tokens: int = 4096,
    effort: str = "low",
) -> ClientLLM:
    fournisseur = (fournisseur or os.getenv("LLM_PROVIDER") or "anthropic").strip().lower()
    if fournisseur not in FOURNISSEURS:
        raise ErreurLLM(
            f"Fournisseur de modèle inconnu : « {fournisseur} ». "
            f"Valeurs acceptées : {' | '.join(FOURNISSEURS)}"
        )
    modele = (modele or os.getenv("LLM_MODEL") or "").strip() or modele_par_defaut(fournisseur)

    if fournisseur == "anthropic":
        return ClientAnthropic(modele, max_tokens, effort)
    return ClientCompatibleOpenAI(fournisseur, modele, max_tokens)
