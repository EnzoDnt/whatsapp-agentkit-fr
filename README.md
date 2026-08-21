# AgentKit FR — un agent WhatsApp installé par votre IA

Vous décrivez votre entreprise. Votre agent de développement (Claude Code, Codex,
Cursor) fait le reste : il installe, configure, branche WhatsApp et teste.

**Vous n'écrivez pas de code. Vous ne suivez pas un tutoriel de 40 étapes.**
Vous répondez à des questions, et on vous dit précisément les rares moments où
vous devez cliquer quelque part vous-même.

---

## Démarrer

Ouvrez votre agent de développement dans un dossier vide et collez ceci :

```
Installe mon agent WhatsApp en suivant les instructions de
https://github.com/EnzoDnt/whatsapp-agentkit-fr/blob/main/AGENTS.md
```

C'est tout. L'agent clone le dépôt, lit sa feuille de route et vous guide.

**Première victoire en 5 minutes** : le simulateur intégré vous montre votre
agent qui répond, sans aucun compte WhatsApp. Une seule chose est nécessaire à
ce stade — une clé API Anthropic.

---

## Le principe

La plupart des kits vous laissent seul face à la console Meta. Ici, la règle est
inscrite dans [`AGENTS.md`](AGENTS.md) et l'agent doit s'y tenir :

> **Il fait tout ce qu'il est capable de faire. Il ne vous sollicite que pour
> l'impossible — et vous dit pourquoi.**

| ✅ Votre agent s'en charge | 🔴 Vous, 30 secondes |
|---|---|
| Installer Python, les dépendances, l'environnement | Créer le compte développeur Meta |
| Générer les secrets et écrire la configuration | Créer l'app Meta + produit WhatsApp |
| Ouvrir un tunnel HTTPS public | Copier l'App Secret et le token |
| **Souscrire et tester le webhook Meta** | Ajouter votre numéro de test |
| Lancer le simulateur et vérifier les réponses | |
| Jouer la suite de tests | |

Avec le connecteur **Meta Developer Tools (MCP)** activé, votre agent lit vos
apps, vérifie leur conformité et configure le webhook lui-même. Sans lui, il
retombe sur la Graph API en `curl` — ça marche aussi.

---

## Ce que l'agent sait faire

Il **agit**, il ne fait pas que discuter. Quatre outils réellement exécutés :

- `rechercher_information` — puise dans vos documents (`knowledge/`) au lieu d'inventer
- `verifier_delai` — refuse une date impossible, **vérifiée en code**, pas par le prompt
- `enregistrer_demande` — écrit la commande sur disque pour votre équipe
- `transferer_a_humain` — escalade une réclamation

---

## Sécurité et RGPD

Pensé pour être déployé chez de vrais clients :

- **Signature obligatoire en production.** Sans `META_APP_SECRET`, le serveur
  refuse de démarrer. Pas de mode « ça passe quand même ».
- **Aucune donnée personnelle en clair dans les logs.** Les numéros sont hachés
  avec un sel (`tel_a3f9c1d2…78`), le contenu des messages n'est jamais écrit.
- **Purge automatique** de l'historique après `RETENTION_JOURS` (90 par défaut),
  et effacement à la demande pour un client (droit à l'effacement).
- **Limite de débit** par numéro et **plafond de dépense** quotidien : un
  importun ne peut pas faire exploser votre facture.
- **Pas d'intermédiaire.** Connexion directe à la WhatsApp Cloud API de Meta :
  aucun tiers ne voit les conversations de vos clients, donc aucun sous-traitant
  supplémentaire à encadrer.

## Conformité Meta 2026

Depuis le 15 janvier 2026, Meta interdit les **assistants IA généralistes** sur
la WhatsApp Business API. Les agents au service d'une entreprise — FAQ,
commandes, rendez-vous, support — restent explicitement autorisés.

Ce kit produit un agent cadré sur une entreprise précise : il est du bon côté.
Ne le transformez pas en assistant généraliste.

---

## Coûts

| Poste | Prix |
|---|---|
| AgentKit FR | Gratuit, MIT |
| WhatsApp Cloud API | Les conversations ouvertes par le client sont gratuites |
| Claude | À l'usage — voir ci-dessous |
| Hébergement | ~5 €/mois (Railway, Fly, VPS) |

Un chatbot ne coûte pas « par message » : à chaque tour, on renvoie à Claude le
prompt système **plus** tout l'historique. Une conversation de 8 messages avec un
prompt de ~1 500 tokens consomme environ 16 000 tokens d'entrée et 1 200 de sortie :

| Modèle | Par conversation | 300/mois | 1 000/mois |
|---|---|---|---|
| Claude Opus 5 | ~0,11 $ | ~33 $ | ~110 $ |
| **Claude Sonnet 5** (défaut) | ~0,07 $ | ~20 $ | ~66 $ |
| Claude Haiku 4.5 | ~0,02 $ | ~7 $ | ~22 $ |

Le meilleur levier n'est pas de changer de modèle : c'est de ne pas mettre dans
le prompt ce que vos clients ne demandent jamais.

---

## Commandes

```bash
make installer    # environnement + dépendances
make simulateur   # http://localhost:8000/simulateur
make test         # suite de tests
make serveur      # production
```

---

## Origine

Dérivé de [Hainrixz/whatsapp-agentkit](https://github.com/Hainrixz/whatsapp-agentkit)
d'Enrique Rocha (MIT), dont l'architecture webhook et la rigueur des prompts ont
servi de base. Cette version ajoute le pilotage par agent, le simulateur local,
le durcissement sécurité/RGPD, l'exécution réelle des outils et la traduction
française.

Licence MIT.
