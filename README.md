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

## Back-office minimal

Volontairement réduit à l'essentiel : lire ce que l'agent a répondu, et reprendre
la main quand il le faut.

- La liste des conversations, la plus récente d'abord
- L'historique complet de chacune
- **Reprendre la main** : l'agent se tait sur cette conversation, vous répondez
  vous-même — et votre réponse entre dans l'historique, pour que l'agent sache
  ce qui a été dit quand il reprend
- Effacer une conversation (droit à l'effacement)

Activez-le en définissant `ADMIN_TOKEN` dans `.env`, puis ouvrez `/admin`. Sans
ce jeton, les routes ne sont pas montées du tout — cette interface expose des
conversations clients.

Le message d'un client est **toujours** conservé, même quand l'agent échoue :
c'est justement là qu'un humain doit intervenir.

---

## Usernames WhatsApp (2026)

Depuis juillet 2026, un client peut vous écrire **sans partager son numéro**.
Meta envoie alors un identifiant BSUID et laisse le champ `from` vide. Beaucoup
d'intégrations lisent uniquement `from` et **ignorent purement et simplement ces
clients**. Le kit ingère les deux.

À savoir : un *username business* ne remplace pas le numéro de téléphone. Il se
réserve après, et ne masque pas le numéro de l'entreprise. Pour ouvrir un compte
WhatsApp Business API, un numéro reste obligatoire.

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
prompt système **plus** tout l'historique. Et quand l'agent utilise ses outils —
chercher un tarif, vérifier un délai — il faut **deux** appels au lieu d'un : le
premier pour demander l'outil, le second pour formuler la réponse.

Les chiffres ci-dessous sont **mesurés en production**, pas estimés : conversation
réelle sur WhatsApp, agent avec ses quatre outils et une base de tarifs.

| Mesure relevée | Valeur |
|---|---|
| Appels à Claude par message client | 2 |
| Tokens d'entrée par message | ~5 000 |
| Tokens de sortie par message | ~360 |
| Coût par message client (Sonnet 5) | **0,020 $** |
| Latence bout en bout | 7 secondes |

| Modèle | Par conversation (8 messages) | 300/mois | 1 000/mois |
|---|---|---|---|
| Claude Opus 5 | ~0,27 $ | ~80 $ | ~270 $ |
| **Claude Sonnet 5** (défaut) | ~0,16 $ | ~49 $ | ~164 $ |
| Claude Haiku 4.5 | ~0,05 $ | ~16 $ | ~55 $ |

> Beaucoup de kits annoncent la moitié de ces montants — en oubliant que les
> outils doublent les appels. Si votre agent se contente de répondre sans jamais
> rien chercher ni vérifier, divisez par deux. Mais alors il invente les prix.

Le meilleur levier n'est pas de changer de modèle : c'est de ne pas mettre dans
le prompt ce que vos clients ne demandent jamais.

---

## Commandes

```bash
make installer    # environnement + dépendances
make simulateur   # http://localhost:8000/simulateur
make test         # les 176 tests
make test-pg      # tests sur un vrai PostgreSQL (démarre un conteneur)
make serveur      # production
```

## Ce qui est vérifié

176 tests automatisés, en quatre couches :

| Couche | Ce qu'elle couvre |
|---|---|
| Unitaire | Masquage RGPD, hachage des mots de passe, consignes datées, tarifs, schémas d'outils |
| Intégration | Toute l'API HTTP réelle : authentification, webhook, conversations, escalades, demandes, documents, prompt, marque |
| Adversarial | Mode production, concurrence, plafond de dépense, pannes du modèle, échec d'envoi, traversée de répertoire, usurpation d'identifiant |
| PostgreSQL | La couche de données sur un PostgreSQL 17 réel, là où il diverge de SQLite |

Les tests PostgreSQL sont ignorés automatiquement si aucune base n'écoute : le kit
s'installe sans Docker.

---

## Origine

Dérivé de [Hainrixz/whatsapp-agentkit](https://github.com/Hainrixz/whatsapp-agentkit)
d'Enrique Rocha (MIT), dont l'architecture webhook et la rigueur des prompts ont
servi de base. Cette version ajoute le pilotage par agent, le simulateur local,
le durcissement sécurité/RGPD, l'exécution réelle des outils et la traduction
française.

Licence MIT.
