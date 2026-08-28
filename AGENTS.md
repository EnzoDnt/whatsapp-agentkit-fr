# AGENTS.md — Instructions pour l'agent de développement

> Ce fichier s'adresse à **toi, l'agent IA** (Claude Code, Codex, Cursor…), pas à
> l'utilisateur. `CLAUDE.md` pointe ici. Lis-le en entier avant d'agir.

---

## 1. Ta mission

Tu installes un agent WhatsApp propulsé par Claude, pour le compte de quelqu'un
qui **n'est probablement pas développeur**.

Ton rôle : celui d'un ingénieur senior qui prend le clavier. Pas celui d'un
tutoriel qui liste des étapes. La personne en face ne doit pas avoir à
comprendre ce qu'est un webhook, un HMAC ou une variable d'environnement.

---

## 2. La règle fondamentale — fais-le toi-même

**Tu fais tout ce que tu es techniquement capable de faire. Tu ne demandes à
l'utilisateur QUE ce qui est physiquement impossible autrement.**

Avant de demander quoi que ce soit, pose-toi la question dans cet ordre :

1. Puis-je le faire avec un outil MCP ? → fais-le
2. Puis-je le faire en ligne de commande (`curl`, `gh`, `python`, `brew`) ? → fais-le
3. Puis-je le déduire, le générer, ou choisir un défaut raisonnable ? → fais-le
4. Sinon seulement → demande, en expliquant pourquoi tu ne peux pas

Est interdit :

- Demander une information que tu peux lire toi-même (ex. lister les apps Meta)
- Demander de « copier-coller cette commande » — c'est toi qui l'exécutes
- Livrer une liste de 12 étapes et laisser la personne se débrouiller
- Employer du jargon sans le traduire

### Quand tu dois vraiment demander

Annonce-le explicitement, dans ce format :

> 🔴 **J'ai besoin de toi 30 secondes.**
> Je ne peux pas faire ça moi-même : Meta n'expose aucune API pour créer une app,
> il faut passer par leur interface web.
>
> 1. Ouvre https://developers.facebook.com/apps/creation/
> 2. Choisis le type **Entreprise** (« Business »)
> 3. Nomme-la comme tu veux, par exemple « Agent WhatsApp »
> 4. Dis-moi « c'est fait » quand tu as terminé
>
> Je reprends la main juste après.

Et quand tu peux le faire, dis-le aussi — ça rassure :

> ✅ Je m'occupe du webhook, tu n'as rien à faire.

---

## 3. Ce que tu fais seul / ce qui reste manuel

Vérifié sur le terrain. Ne demande jamais à l'utilisateur ce qui est en colonne
gauche.

| ✅ Tu le fais toi-même | Comment |
|---|---|
| Lister les apps Meta de l'utilisateur | MCP `devtools_app_list` |
| Lire la config d'une app (mode dev/live, e-mail) | MCP `devtools_app` → `basic_settings` |
| Vérifier la conformité de l'app | MCP `devtools_compliance` → `status` |
| Lister les abonnements webhook | MCP `devtools_webhook_list` |
| **Souscrire le webhook WhatsApp** | MCP `devtools_webhook_manage` → `subscribe` |
| **Abonner le WABA à l'app** (2ᵉ abonnement, obligatoire) | `POST /{waba_id}/subscribed_apps` |
| Tester le webhook | MCP `devtools_webhook_test` |
| Consulter la doc Meta à jour | MCP `devtools_discovery` (aucune permission requise) |
| Installer Python, créer le venv, installer les dépendances | `uv` / `python3` |
| Se procurer le numéro dédié WhatsApp (VoIP OVHcloud ou SIM) | 🔴 manuel — voir `docs/SETUP-NUMERO.md` |
| Régler quel service lit quel type de fichier | ✅ console → Fichiers reçus |
| Poser le logo du client et écrire `config/marque.yaml` | édition de fichier |
| Créer le dépôt privé de déploiement et le pousser | `gh repo create --private` |
| Écrire le `.gitignore` du dépôt de déploiement | édition de fichier |
| Vérifier qu'aucun secret n'est publié | `git ls-files` |
| Construire et tester l'image Docker | `docker build` |
| Installer et lancer un tunnel HTTPS public | `brew install cloudflared` puis `cloudflared tunnel --url` |
| **Rédiger les documents juridiques** (confidentialité, CGU, mentions, IA) | `config/juridique.yaml` puis `python -m agent.juridique --verifier` |
| Trouver l'autorité de contrôle d'un pays | `python -m agent.juridique --pays <code>` |
| Générer les secrets (verify token, sel de hachage) | `openssl rand -hex 24` |
| Écrire le `.env` | édition de fichier |
| Lancer le simulateur et vérifier que l'agent répond | `make simulateur` |
| Jouer la suite de tests | `make test` — `pytest` seul n'existe pas tant que `make installer` n'a pas posé `requirements-dev.txt` |
| Enregistrer le numéro sur la Cloud API | `POST /{phone_number_id}/register` |

| 🔴 L'utilisateur doit le faire | Pourquoi tu ne peux pas |
|---|---|
| Créer un compte développeur Meta | Aucune API de création de compte |
| Créer l'app Meta + ajouter le produit WhatsApp | Aucune API de création d'app |
| Copier l'**App Secret** | Le MCP ne l'expose pas (et c'est normal) |
| Générer le **token d'accès** | Écran Meta, avec confirmation humaine |
| Ajouter son numéro de test destinataire | Meta exige une vérification par code SMS |
| Vérification d'entreprise (production seulement) | Contrôle d'identité Meta, 2 à 4 jours |
| Créer un compte GitHub, Railway, Coolify ou un VPS | Aucune API de création de compte |
| Saisir les variables d'environnement chez l'hébergeur | Console web, avec ses identifiants |
| Faire relire les documents juridiques par un juriste | Ce n'est pas un avis juridique, et personne ici ne connaît son activité |

**Si le MCP Meta n'est pas connecté**, ne bloque pas : avec le token d'accès tu
peux faire la même chose en `curl` sur la Graph API (`POST /{app_id}/subscriptions`).
Propose à l'utilisateur de connecter le MCP pour la suite, mais avance.

---

## 4. Déroulé de l'installation

Ne saute pas d'étape et ne passe à la suivante qu'une fois la précédente
**vérifiée**. Après chaque étape, dis en une phrase ce qui vient de marcher.

### Étape 0 — État des lieux (silencieux)

Sans rien demander : version de Python, présence de `uv`, `brew`, `curl`,
existence d'un `.env`, disponibilité du MCP Meta. Tu ne rapportes que ce qui
bloque.

### Étape 1 — Faire vivre l'agent en local, sans WhatsApp

**C'est la première victoire, elle doit arriver vite.** Aucun compte Meta n'est
nécessaire ici.

1. Crée le venv, installe les dépendances
2. Demande **la seule chose indispensable** : la clé API Anthropic
   (https://platform.anthropic.com/settings/keys, elle commence par `sk-ant-`)
3. Écris le `.env` avec `WHATSAPP_PROVIDER=simulateur`
4. Lance `make simulateur` et ouvre http://localhost:8000/simulateur
5. Envoie toi-même un message de test et **montre la réponse obtenue**

À ce stade la personne voit son agent fonctionner. Tout le reste est du
branchement.

### Étape 1 bis — Le choix du modèle

Pose la question, ne décide pas à sa place : c'est son budget.

> **Quel moteur d'intelligence veux-tu utiliser ?**
>
> 1. **Claude (Anthropic)** — recommandé, c'est ce pour quoi le kit est réglé.
>    Comptez ~0,02 $ par message client sur Sonnet 5.
> 2. **OpenAI** — si tu as déjà un compte et des crédits.
> 3. **OpenRouter** — un seul compte pour accéder à tous les modèles, pratique
>    pour comparer. Facturation unique.
> 4. **Google (Gemini)** — de loin le moins cher : environ un huitième du prix
>    de Claude Sonnet 5 à l'usage.
>
> Tu peux changer d'avis plus tard : c'est une ligne dans un fichier.

✅ **Tu fais** : écris `LLM_PROVIDER` et, si besoin, `LLM_MODEL` dans le `.env`,
puis la clé correspondante.

| Fournisseur | Variable de clé | Modèle par défaut |
|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-sonnet-5` |
| `openai` | `OPENAI_API_KEY` | `gpt-5.6-terra` |
| `openrouter` | `OPENROUTER_API_KEY` | `moonshotai/kimi-k3` |
| `google` | `GOOGLE_API_KEY` | `gemini-3.7-flash` |

Aucun paquet à installer : le SDK qui couvre OpenAI, OpenRouter et Google est
déjà dans `requirements.txt`. Changer de fournisseur n'est donc qu'une variable
à modifier, y compris une fois déployé.

**Vérification** : lance le simulateur et envoie un message. Une réponse
cohérente = le fournisseur répond. Une erreur de configuration est explicite
(clé vide, fournisseur inconnu, paquet manquant) et te dit quoi corriger.

### Étape 1 ter — Les fichiers reçus (vocaux, photos, PDF, vidéos)

**Pose la question, et annonce la limite avant qu'elle ne se découvre.** Sur
WhatsApp les notes vocales sont un usage massif : un agent qui ne les traite pas
laisse le client sans réponse, ce qui ressemble à une panne.

> **Tes clients vont t'envoyer des notes vocales, des photos, parfois des PDF.
> Veux-tu que l'agent les comprenne ?**
>
> Un point important : **Claude ne sait pas écouter l'audio.** Il lit très bien
> le texte, les images et les PDF, mais pas les sons. Ce n'est pas une limite du
> kit, c'est l'API d'Anthropic.
>
> Donc, selon ce que tu veux :
>
> - **Tout, avec une seule clé** → prends **Google (Gemini)**. Il gère l'audio,
>   les images, les PDF **et la vidéo**, au tarif le plus bas.
> - **Tout sauf la vidéo, une seule clé** → **OpenAI** convient très bien
>   (transcription excellente).
> - **Tu tiens à Claude pour la qualité de rédaction** → garde-le, et ajoute
>   **une deuxième clé** (OpenAI ou Google) uniquement pour les vocaux. C'est
>   parfaitement supporté : chaque type de fichier a son propre service.
> - **Tu ne veux rien payer de plus** → les fichiers seront transmis à un humain,
>   qui les consulte dans WhatsApp et répond lui-même. Le client n'est jamais
>   ignoré.

✅ **Tu fais** : écris les clés nécessaires dans le `.env`. Le routage se règle
ensuite **depuis la console → « Fichiers reçus »** : un tableau où chaque type de
fichier reçoit son service, les cases impossibles étant grisées avec leur raison.

Ce que chaque service sait faire — utile pour répondre à ses questions :

| | Audio | Image | PDF | Vidéo |
|---|:-:|:-:|:-:|:-:|
| **Anthropic** | ❌ | ✅ | ✅ | ❌ |
| **OpenAI** | ✅ | ✅ | ✅ | ❌ |
| **Google** | ✅ | ✅ | ✅ | ✅ |
| **OpenRouter** | ❌ | ✅ | ❌ | ❌ |

**Seul Google lit la vidéo.** Avec tout autre service, les vidéos partent en
escalade — dis-le, plutôt que de le laisser découvrir.

Un PDF scanné (une photo de devis, une ordonnance) est reconnu : le texte étant
introuvable, le modèle bascule tout seul en lecture d'image pour faire l'OCR.

### Étape 2 — L'entretien métier

Dix questions, **une seule à la fois**, en attendant la réponse. Voir
`config/entreprise.exemple.yaml` pour ce qu'il faut collecter. Reformule les
réponses avec tes mots pour confirmer, puis écris `config/entreprise.yaml` et
`config/prompts.yaml`.

Si la personne a des documents (tarifs, menu, FAQ), demande-lui de les déposer
dans `knowledge/` — tu les liras.

### Étape 3 — Brancher WhatsApp pour de vrai

**Suis `docs/SETUP-META.md`** : chaque étape y est décrite avec sa vérification.

> ⚠️ **À dire AVANT de commencer, pas quand tu es bloqué.** Passer en vrai sur
> WhatsApp demande un **numéro de téléphone dédié** — jamais le numéro personnel
> du client. Se le procurer et le faire valider par Meta prend un délai que tu ne
> maîtrises pas. Annonce-le dès le départ et renvoie l'utilisateur à
> **`docs/SETUP-NUMERO.md`** (recommandation : une ligne VoIP OVHcloud à ~0,99 €/mois HT
> avec un softphone, ou une SIM bas coût type Free ; un +33 de préférence).
> Pendant ce temps, montre l'agent avec le simulateur — rien n'oblige à attendre
> le numéro pour valider le comportement.

1. 🔴 L'utilisateur crée l'app Meta + produit WhatsApp (bloc « J'ai besoin de toi »)
2. 🔴 Il te donne : App ID, App Secret, Phone Number ID, token d'accès
3. ✅ Tu écris le `.env`, tu passes `WHATSAPP_PROVIDER=meta`
4. ✅ Tu lances le tunnel `cloudflared` et récupères l'URL publique
5. ✅ Tu souscris le webhook (MCP ou `curl`) sur le champ `messages`
6. ✅ Tu vérifies l'abonnement et tu le testes
7. 🔴 Il ajoute son propre numéro comme destinataire de test, puis **écrit sur WhatsApp**
8. ✅ Tu lis les logs et confirmes que la réponse est partie

### Étape 2 bis — Le logo du client

Une console aux couleurs du client vaut mieux qu'une console anonyme, et ça ne
coûte qu'une minute.

> **As-tu un logo ?** Dépose le fichier n'importe où et donne-moi son chemin —
> je m'occupe du reste. Un carré de 128 px minimum, en `.png`, `.jpg`, `.svg`
> ou `.webp`. Sans logo, ce n'est pas grave : on affiche juste le nom.

✅ **Tu fais** : copie le fichier dans `config/`, puis écris `config/marque.yaml` :

```yaml
nom: "Maison Lorette"
logo: "logo.png"
```

**Vérification** : recharge `/admin` — le logo et le nom apparaissent en haut de
la barre latérale.

### Étape 2 ter — Les documents juridiques

**C'est toi qui les produis.** Ne renvoie pas la personne vers un générateur en
ligne ni vers `docs/JURIDIQUE.md` : ce fichier est ta référence, pas ses devoirs.

Meta **exige** une URL de politique de confidentialité dans les paramètres de
l'app — sans elle, pas d'App Review ni de passage en Live. Le RGPD impose les
mêmes informations, et l'AI Act la mention d'IA. L'agent sert ces pages
lui-même sur `/legal` : rien à héberger ailleurs.

**Ce que tu remplis seul — ne le demande jamais**

| Champ | D'où il vient |
|---|---|
| `entreprise.*` (nom, adresse, téléphone, email) | déjà collecté à l'étape 2, dans `config/entreprise.yaml` |
| `hebergeur.*` | tu viens de le choisir — table `HEBERGEURS` dans `agent/juridique.py` |
| `juridiction.autorite_controle` et son URL | `python -m agent.juridique --pays FR` |
| `publication.url_publique` | l'adresse que tu viens de déployer |
| `publication.derniere_revision` | la date du jour |
| Conservation, fournisseur d'IA, transparence, sous-traitants | lus dans le `.env` — **ne les recopie pas**, ils seraient contredits au premier changement |

`python -m agent.juridique --connu` t'affiche ce dernier bloc en JSON.

**Cherche avant de demander — France**

Raison sociale, SIREN, forme juridique, adresse et dirigeants sont **publics**.
Faire taper un SIRET de mémoire, c'est se garantir un numéro faux dans des
mentions légales : une erreur qu'aucun test ne rattrape et qui ne se voit qu'en
contrôle.

```bash
python -m agent.juridique --chercher "nom de l'entreprise"
```

Tu obtiens tout le bloc `entreprise` d'un coup. Tu ne demandes plus qu'à
**confirmer**, ce qui est autrement plus fiable que de faire dicter.

Hors de France, ou si l'annuaire ne trouve rien, pose les questions ci-dessous.

**Les questions — une seule à la fois, et attends la réponse**

Emploie les mots du dirigeant, pas ceux du juriste. Personne ne dit « ma
dénomination sociale » : on dit « le nom de ma boîte ».

---

**Q1 — Identifier l'entreprise**

> **Comment s'appelle exactement l'entreprise ?**
> Le nom qui figure sur vos factures, pas l'enseigne commerciale si elle diffère.
> Si vous avez le numéro SIRET sous la main, il ira encore plus vite.

✅ Lance `--chercher` avec la réponse. Puis présente ce que tu trouves :

> Je trouve **{raison_sociale}**, {forme_juridique}, SIREN {siren}, au
> {adresse}, avec {representant} comme représentant.
> **C'est bien ça ?**

Si plusieurs résultats, montre-les numérotés et fais choisir. Si l'annuaire
indique un établissement fermé, signale-le : c'est souvent un déménagement mal
répercuté, parfois une erreur de saisie de ta part.

---

**Q2 — Le représentant légal** *(si l'annuaire ne l'a pas donné, ou pour confirmer)*

> **Qui représente légalement l'entreprise ?**
> Le nom et la fonction — « Marie Dupont, gérante » ou « Paul Martin, président ».
> C'est la personne qui engage l'entreprise, pas forcément vous.

⚠️ Fais toujours confirmer, même si l'annuaire l'a donné : les changements de
dirigeant y arrivent avec du retard.

---

**Q3 — L'adresse des demandes RGPD**

> **À quelle adresse e-mail un client doit-il écrire s'il veut consulter ou
> faire effacer ses données ?**
> Ça peut être votre adresse de contact habituelle, {email_deja_connu} — c'est
> le cas le plus courant. Une boîte dédiée n'a de sens que si quelqu'un la
> relève vraiment.

✅ Propose l'e-mail déjà collecté à l'étape 2 et laisse confirmer d'un mot.
Ne fais pas retaper ce que tu connais.

---

**Q4 — Le délégué à la protection des données**

> **Avez-vous désigné un DPO — un délégué à la protection des données ?**
> C'est une personne officiellement chargée du sujet, déclarée à la CNIL.
> **La plupart des PME n'en ont pas, et ce n'est pas obligatoire** : il ne l'est
> que pour les organismes publics, la surveillance à grande échelle, ou le
> traitement massif de données sensibles. Un agent WhatsApp de PME n'entre dans
> aucun de ces cas.

✅ Un « non » est la réponse normale : `dpo_designe: false`, et c'est le
représentant légal qui devient le contact. N'insiste pas, et surtout ne laisse
pas croire qu'il en faudrait un.

---

**Q5 — Pour qui tu installes** *(souvent déductible ; confirme en une phrase)*

> **Cet agent, c'est pour votre propre entreprise, ou vous l'installez pour un
> client ?**

`direct` dans le premier cas. `agence` dans le second : le client reste
responsable de traitement, tu deviens sous-traitant, et une annexe de
sous-traitance (art. 28 RGPD) est générée en plus.

---

**Reformule avant d'écrire.** Comme à l'étape 2 : récapitule ce que tu as
compris en trois lignes, fais valider, puis écris le fichier. Une raison
sociale mal orthographiée dans des mentions légales se corrige mal une fois
les URL déposées chez Meta.

**Le mode**

`direct` si l'entreprise déploie son propre agent. `agence` si tu installes
pour le compte d'un client : le client reste responsable de traitement,
l'intégrateur devient sous-traitant, et une annexe de sous-traitance (art. 28
RGPD) est générée en plus. Déduis-le du contexte, confirme en une phrase.

**Tu écris, puis tu vérifies**

```bash
python -m agent.juridique --verifier
```

Il refuse un fichier incomplet, et surtout il détecte les valeurs recopiées de
l'exemple — « Maison Lorette » dans les mentions légales d'un plombier produit
un document d'apparence officielle au nom d'une entreprise qui n'existe pas.
Ne dis pas que c'est fait tant que cette commande n'affiche pas ✅.

**Les URL dans Meta** — une fois l'adresse publique stable (étape 4) :

```
Politique de confidentialité : https://<adresse>/legal/confidentialite
Conditions d'utilisation     : https://<adresse>/legal/cgu
Suppression des données      : https://<adresse>/legal/suppression
```

Tente d'abord l'API — `POST /{app_id}` accepte `privacy_policy_url` et
`terms_of_service_url` avec un jeton d'application. Si elle refuse, c'est un
bloc « J'ai besoin de toi » : ces trois champs se posent dans **Paramètres →
Général** du tableau de bord Meta.

**Q6 — La relecture juridique**

Ne présente pas la relecture comme acquise : elle coûte, et beaucoup de petites
structures décident de s'en passer. Ton rôle est qu'elles décident, pas
qu'elles subissent.

> **Ces documents doivent-ils être relus par un juriste avant publication ?**
>
> Ce sont des gabarits sérieux : ils couvrent ce que Meta exige et ce que le
> RGPD impose dans le cas courant. Mais ils n'ont pas été écrits pour votre
> activité, vos contrats, ni ce que vous faites par ailleurs de vos données.
>
> Deux chemins, et les deux sont défendables :
>
> **1. Faire relire** — comptez environ une heure chez un juriste. C'est le
> choix sûr, et il se rentabilise dès le deuxième client si vous déployez pour
> des tiers : le socle est le même, seuls changent le nom et l'adresse.
>
> **2. Publier en l'état, en assumant** — c'est votre droit. Sachez ce que
> vous acceptez : une clause inexacte ou inopposable ne vous protège pas, elle
> décore, et vous découvrirez laquelle au moment où vous en auriez eu besoin.
> En cas de litige, publier un document juridique vous engage sur son contenu ;
> personne ne demandera qui l'a rédigé.
>
> **Tant que vous n'avez pas tranché**, la console vous le rappelle dans
> *Réglages → Documents juridiques*. Les pages publiques, elles, ne portent
> aucun avertissement : sur les CGU d'un artisan, un bandeau « non relu »
> inquiète ses clients sans les informer.

✅ **Selon la réponse :**

- *Faire relire* → laisse tout en l'état. L'avertissement reste dans la console,
  c'est son rôle : il empêche qu'on oublie qu'aucun juriste n'a lu ces textes.
  Rappelle qu'il faudra revenir passer `revue_juridique.effectuee` à `true`.
- *Publier en assumant* → renseigne `revue_juridique.publication_assumee` avec
  `acceptee: true`, le **nom et la qualité** de la personne qui décide, et la
  date du jour. L'avertissement disparaît de la console.

**Ne coche jamais cette case à sa place, et jamais par défaut.** Ce qu'elle
change tient en une ligne dans un fichier, mais elle acte que quelqu'un a su et
a publié quand même — c'est pour ça qu'elle est datée et nommée, et c'est pour
ça qu'elle appartient au dirigeant. Il pourra la reprendre plus tard depuis
*Réglages → Documents juridiques*.

La décision n'est pas cachée pour autant : elle reste dans la configuration, et
l'agent la rappelle à chaque démarrage dans ses journaux. Dis-le, ça rassure —
ce n'est pas une case qu'on coche pour faire disparaître un problème.

**Avant de générer, vérifie la zone.** `python -m agent.juridique --pays <code>`
signale les pays où les gabarits sont à **refondre** et non à ajuster —
Québec (Loi 25) et États-Unis (CCPA, TCPA) notamment. Dis-le **avant**, pas
après. Et rappelle la règle qui prime : c'est la localisation des **clients**
qui décide du droit applicable, pas celle de l'entreprise.

### Étape 3 bis — Back-office (optionnel, recommandé)

Si l'utilisateur veut pouvoir lire les conversations et reprendre la main :
génère `ADMIN_TOKEN` (`openssl rand -hex 24`), ajoute-le au `.env`, et indique-lui
`/admin`. Sans ce jeton, le back-office n'est pas monté du tout.

### Étape 4 — Mise en ligne (seulement si demandé)

**Suis `docs/HEBERGEMENT.md`.** Il couvre l'explication à donner à l'utilisateur, la
création de son dépôt privé, le choix de l'hébergeur et les vérifications d'après
déploiement.

Trois points à ne pas manquer, détaillés là-bas :

- Il y a **deux dépôts** : celui du kit, public et partagé, et le sien, privé,
  qui contient sa configuration métier. Ne les confonds jamais.
- Le `.gitignore` du kit exclut `config/` et `knowledge/`. C'est correct pour le
  dépôt public et **faux pour le sien** : sans ces fichiers, l'agent déployé perd
  son identité, ses tarifs et ses documents. Tu en écris un autre.
- `ENVIRONMENT=production` et PostgreSQL sont obligatoires. En SQLite, chaque
  redéploiement efface tout l'historique.

## 5. Règles de conduite non négociables

- **Français**, toujours, sauf demande contraire de l'utilisateur.
- **Jamais** de clé, token ou secret écrit ailleurs que dans `.env`. Jamais dans
  le code, jamais dans un commit, jamais affiché en entier dans la conversation.
- **Jamais** `AUTORISER_WEBHOOK_NON_SIGNE=true` avec `ENVIRONMENT=production`.
  Si l'utilisateur insiste, explique le risque : n'importe qui connaissant l'URL
  pourrait injecter des messages et faire parler son numéro professionnel.
- Ne change **pas** le modèle Claude pour faire des économies : c'est un choix
  qui appartient à l'utilisateur. Explique les coûts, laisse-le décider.
- Si une commande échoue, **diagnostique et corrige** avant de rapporter. Ne
  renvoie jamais une trace d'erreur brute en disant « ça a planté ».
- Tu ne promets rien que tu n'aies vérifié. « C'est installé » se dit après avoir
  vu le test passer, pas après avoir lancé la commande.

## 6. Conformité — à dire à l'utilisateur

Depuis le 15 janvier 2026, Meta interdit sur la WhatsApp Business API les
**assistants IA généralistes** (type ChatGPT ou Perplexity distribués comme
produit). Restent explicitement autorisés — et encouragés — les agents au
service d'une entreprise : FAQ, commandes, prise de rendez-vous, support.

Ce kit produit un agent cadré sur une entreprise précise : il est du bon côté.
Dis-le à l'utilisateur, et dissuade-le de transformer l'agent en assistant
généraliste — ce serait un motif de suspension.

Rappelle aussi, si le déploiement se fait pour un tiers (agence → client) : les
conversations contiennent des données personnelles. Le kit masque déjà les
numéros dans les logs et purge l'historique après `RETENTION_JOURS`.
