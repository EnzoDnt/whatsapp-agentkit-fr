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

## Deux dépôts, pas un

Une distinction à avoir en tête dès le départ :

| | **Ce dépôt** | **Le vôtre** |
|---|---|---|
| Visibilité | Public, partagé par tous | Privé, créé à la fin |
| Contenu | Le code et les instructions | Votre code **et votre configuration métier** |
| Rôle | Ce que vous clonez pour démarrer | Ce que l'hébergeur déploie |

Tant que vous testez, un seul dépôt suffit : celui-ci. Le second n'arrive qu'au
moment de mettre l'agent en ligne, et **votre agent le crée pour vous** — voir
[`HEBERGEMENT.md`](HEBERGEMENT.md).

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
- `passer_la_main` — escalade une réclamation

---

## Deux exemples complets

Le kit livre deux configurations métier de bout en bout — voir
[`exemples/README.md`](exemples/README.md).

- **Maison Lorette**, boulangerie-pâtisserie : la version minimale, dans
  `config/*.exemple.yaml`. Elle montre la structure des fichiers.
- **Mario les Bons Tuyaux**, plomberie : la version complète, dans
  `exemples/plomberie/`. Sept documents, une zone d'intervention commune par
  commune, des consignes de sécurité, un périmètre métier explicite.

Le second vaut surtout pour ce qu'il montre de la **rédaction** : la recherche
dans `knowledge/` est littérale et travaille ligne par ligne, ce qui impose
d'écrire un fait par ligne, avec les synonymes et les variantes accentuées.
Un intitulé séparé de son prix par un retour à la ligne, et le modèle reçoit
une moitié d'information — puis comble le vide.

---

## Ce que l'agent reçoit

Sur WhatsApp, un client n'écrit pas toujours. Il envoie un vocal en marchant,
photographie un devis, transfère un PDF. Un agent qui ne lit que le texte
**répond à côté ou ne répond pas** — et personne ne le voit, parce qu'il n'y a
pas d'erreur : juste un silence.

L'agent convertit chaque fichier en texte avant de réfléchir :

| Ce que le client envoie | Ce que l'agent en fait |
|---|---|
| Note vocale | Transcription (Ogg/Opus transcodé en WAV par ffmpeg) |
| Photo | Description de l'image, légende comprise |
| Vidéo | Analyse image **et** bande son |
| PDF | Extraction du texte — et **OCR** si le PDF est un scan |

**Chaque type a son propre fournisseur et son propre modèle**, réglables dans le
back-office. C'est nécessaire, pas décoratif : Claude lit les images et les PDF
mais **ne sait pas écouter un audio**, et seul Gemini analyse une vidéo
nativement. Le tableau grise tout seul les cases impossibles et celles dont la
clé API n'est pas renseignée.

Et si un type n'est pas configuré — ou si la conversion échoue — l'agent
**n'improvise pas** : il met la conversation en pause et la passe à un humain,
avec le fichier consultable dans le back-office. Le filet plutôt que le silence.

Détails, limites et coûts : **[SPEC-MEDIAS.md](SPEC-MEDIAS.md)**.

---

## Back-office minimal

Volontairement réduit à l'essentiel : lire ce que l'agent a répondu, et reprendre
la main quand il le faut.

- La liste des conversations, la plus récente d'abord
- L'historique complet de chacune, **qui se met à jour tout seul** : un message
  qui arrive apparaît sans recharger la page, et sans interrompre le vocal que
  vous étiez en train d'écouter
- Les fichiers reçus **consultables sur place** — le vocal s'écoute, la photo et
  la vidéo se regardent, avec la transcription juste en dessous
- **Reprendre la main** : l'agent se tait sur cette conversation, vous répondez
  vous-même — et votre réponse entre dans l'historique, pour que l'agent sache
  ce qui a été dit quand il reprend
- Le **tableau des fichiers reçus** : quel fournisseur et quel modèle pour le
  vocal, la photo, la vidéo, le PDF — la liste des modèles est interrogée en
  direct chez chaque fournisseur, vous choisissez dans ce qui existe vraiment
- Les messages types (incompréhension, panne technique, quota) modifiables sans
  toucher au code
- Effacer une conversation (droit à l'effacement)

Activez-le en définissant `ADMIN_TOKEN` dans `.env`, puis ouvrez `/admin`. Sans
ce jeton, les routes ne sont pas montées du tout — cette interface expose des
conversations clients.

Le message d'un client est **toujours** conservé, même quand l'agent échoue :
c'est justement là qu'un humain doit intervenir.

---

### Documents juridiques

**Réglages → Documents juridiques.** Les six pages publiées par l'agent y sont
listées avec leur lien, et c'est de là que se prend la seule décision qui
revient à l'exploitant : afficher ou non l'avertissement « Document non relu
par un professionnel du droit » sur des pages que ses clients consultent.

Trois états, et un seul affiche le bandeau : rien de tranché *(défaut)*,
publication assumée sans relecture, ou relecture faite. Les deux derniers
exigent un **nom** — retirer un avertissement juridique engage, la décision
ne peut pas être anonyme. Elle est datée dans `config/juridique.yaml` et
rappelée à chaque démarrage dans les journaux.

L'écriture ne touche que le bloc `revue_juridique` : les commentaires du
fichier sont préservés, car ce sont eux qui portent les avertissements.

### Mot de passe de console perdu

Il n'y a pas d'envoi d'e-mail de réinitialisation : l'agent n'a pas de serveur
de courrier, et lui en donner un pour ce seul usage ajouterait un service à
maintenir. La récupération se fait **sur le serveur**, là où la base est
joignable.

```bash
# Lister les comptes — l'adresse doit correspondre EXACTEMENT
python -m agent.auth --lister

# Reposer un mot de passe
python -m agent.auth --reinitialiser vous@exemple.fr
```

`ADMIN_TOKEN` doit être défini dans l'environnement — c'est le cas sur un
serveur correctement configuré — mais n'est pas redemandé : la commande tourne
dans le conteneur, où la variable est de toute façon lisible.

Le nouveau mot de passe est saisi à l'invite, jamais en argument : un argument
reste dans l'historique du shell et dans la liste des processus, visible par
tout autre utilisateur de la machine.

**Dans un terminal web** (Coolify, Portainer) il n'y a pas de vrai TTY, et
aucune invite ne peut s'afficher. Passez alors le mot de passe par
l'environnement :

```bash
AGENTKIT_NOUVEAU_MDP='votre-mot-de-passe' \
  python -m agent.auth --reinitialiser vous@exemple.fr
```

La commande détecte l'absence de TTY et affiche cette ligne toute faite plutôt
que de bloquer devant un curseur qui n'attend rien.

Le compte est réactivé au passage. Réinitialiser sans réactiver laisserait la
personne dehors sans lui dire pourquoi : le mot de passe serait bon, et la
connexion refusée quand même.

**Sur Docker ou Coolify**, la commande s'exécute dans le conteneur de l'agent :

```bash
docker compose exec agent python -m agent.auth --lister
```

À savoir : la console affiche le **même message** pour une adresse inconnue et
un mot de passe faux — préciser lequel révélerait quelles adresses ont un
compte. En cas de doute sur l'adresse, `--lister` est le seul moyen de
trancher.

## Usernames WhatsApp (2026)

Depuis juillet 2026, un client peut vous écrire **sans partager son numéro**.
Meta envoie alors un identifiant BSUID et laisse le champ `from` vide. Beaucoup
d'intégrations lisent uniquement `from` et **ignorent purement et simplement ces
clients**. Le kit ingère les deux.

À savoir : un *username business* ne remplace pas le numéro de téléphone. Il se
réserve après, et ne masque pas le numéro de l'entreprise. Pour ouvrir un compte
WhatsApp Business API, un numéro **dédié** reste obligatoire — jamais celui du
client. Comment le choisir et où le prendre à bas coût (VoIP OVHcloud ou SIM) :
**[`SETUP-NUMERO.md`](SETUP-NUMERO.md)**. C'est l'étape la plus longue de la mise
en production, à lancer tôt.

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
| Conversion des fichiers reçus | À l'usage — quelques millièmes d'euro par fichier |
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

Les fichiers reçus s'ajoutent à cela, mais restent marginaux : un vocal de 30 s
coûte entre 0,0004 $ et 0,002 $ selon le fournisseur, une photo ~0,007 $. Le
garde-fou `PLAFOND_DEPENSE_JOUR` compte ces conversions **avec** le reste : une
photo envoyée en boucle ne peut pas vider votre compte pendant la nuit.

---

## Mettre en ligne

Tant que l'agent tourne sur votre ordinateur, il s'arrête quand vous le fermez.
Pour qu'il réponde le dimanche à 22 h, il lui faut un serveur.

Deux voies, décrites dans [`HEBERGEMENT.md`](HEBERGEMENT.md) :

- **Railway** — vous connectez votre dépôt, ça se déploie seul. ~5 $/mois. La voie
  rapide pour un premier agent.
- **Coolify sur votre serveur** — ~5 €/mois pour *plusieurs* agents, données en
  Europe. La voie souveraine, plus exigeante à mettre en place.

Le kit fournit le `Dockerfile` et le `docker-compose.yaml` : les deux hébergeurs les
lisent tels quels.

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

Licence MIT.
