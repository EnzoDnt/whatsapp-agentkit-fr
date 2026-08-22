# Brancher WhatsApp — procédure vérifiée

> Destinataire : **l'agent IA**. Chaque étape a une vérification. N'avance
> jamais sans l'avoir passée. Si une vérification échoue, corrige — ne continue pas.

**Ce qui est automatisé et ce qui ne peut pas l'être.** Meta n'expose aucune API
pour créer un compte, créer une app ou lire un App Secret : ces trois points
resteront manuels quoi qu'il arrive. Tout le reste, tu le fais.

---

## Étape 0 — De quoi dispose l'utilisateur

Sans rien lui demander, établis l'état des lieux.

```bash
python3 --version && command -v uv brew curl openssl
```

Puis teste le MCP Meta : appelle `devtools_app_list`.

| Résultat | Ce que tu fais |
|---|---|
| Une liste d'apps s'affiche | ✅ MCP actif. Demande laquelle utiliser, ou propose d'en créer une. |
| Erreur d'authentification | Le connecteur **Meta Developer Tools** n'est pas branché. Guide sa connexion, ou continue en `curl` avec le token (tout marche aussi). |
| Liste vide | L'utilisateur a un compte Meta mais aucune app, ou n'a pas accordé l'accès. Va à l'étape 1. |

> ⚠️ Une app absente de la liste n'est pas forcément inexistante : l'utilisateur
> peut simplement ne pas lui avoir accordé l'accès sur l'écran de consentement.
> Demande-le-lui avant de conclure qu'il faut en créer une.

---

## Étape 1 — Compte et app Meta 🔴 manuel

> 🔴 **J'ai besoin de toi, environ 3 minutes.**
> Meta ne permet pas de créer une app par API — c'est le seul moment où je ne
> peux rien faire à ta place.
>
> 1. Va sur https://developers.facebook.com/ et connecte-toi (ou crée un compte,
>    c'est gratuit et ça réutilise ton compte Facebook)
> 2. **Mes apps** → **Créer une app**
> 3. Cas d'usage : choisis **Autre**, puis type **Entreprise**
> 4. Donne-lui un nom, par exemple « Agent WhatsApp »
> 5. Dans le tableau de bord, trouve **WhatsApp** et clique **Configurer**
> 6. Donne-moi l'**App ID** (le numéro affiché en haut de la page)
>
> Je reprends la main juste après.

**Vérification — tu la fais toi-même**, via `devtools_app` → `basic_settings` :

```
app_id présent, app_status = "dev_mode"
```

Le mode développement est normal et suffisant pour tout ce qui suit.

---

## Étape 2 — Identifiants 🔴 manuel

> 🔴 **Deux valeurs à copier, environ 2 minutes.**
> Le MCP ne donne pas accès à l'App Secret — c'est volontaire côté Meta, c'est
> une bonne chose pour ta sécurité.
>
> 1. **App Secret** : Paramètres de l'app → Général → App Secret → **Afficher**
> 2. **Token d'accès** : WhatsApp → Configuration de l'API → **Générer un token**
>    (il expire dans 24 h — parfait pour tester, on fera un token permanent après)
> 3. Note aussi le **Phone Number ID** et le **WhatsApp Business Account ID**
>    affichés juste en dessous
>
> Colle-les-moi ici, je les écris dans `.env` et nulle part ailleurs.

✅ **Tu fais** : génère le verify token et le sel de hachage, écris le `.env`.

```bash
echo "META_VERIFY_TOKEN=$(openssl rand -hex 16)" >> .env
echo "PII_HASH_SALT=$(openssl rand -hex 24)" >> .env
```

**Vérification** — le token est-il valide ?

```bash
curl -s "https://graph.facebook.com/v25.0/$META_PHONE_NUMBER_ID?fields=display_phone_number,verified_name,quality_rating" \
  -H "Authorization: Bearer $META_ACCESS_TOKEN"
```

Un JSON avec `display_phone_number` = ✅. Une erreur `190` = token expiré ou faux,
retourne à l'étape 2.

---

## Étape 3 — URL publique ✅ automatique

Le webhook doit être joignable depuis internet en HTTPS. En local, un tunnel suffit.

```bash
command -v cloudflared || brew install cloudflared
cloudflared tunnel --url http://localhost:8000
```

Récupère l'URL `https://xxx.trycloudflare.com` affichée. Elle change à chaque
lancement : garde le tunnel ouvert pendant tout le test.

**Vérification** — le serveur répond-il à travers le tunnel ?

```bash
curl -s https://xxx.trycloudflare.com/
```

Attendu : `{"statut":"ok",...}` ou `"degrade"`. Un 502 = le serveur local n'est
pas démarré (`make serveur`).

---

## Étape 4 — Souscrire le webhook ✅ automatique

C'est l'étape où la plupart des gens abandonnent. Tu la fais entièrement.

**Avec le MCP** — `devtools_webhook_manage` :

```
action:        subscribe
app_id:        <APP_ID>
topic:         whatsapp_business_account
callback_url:  https://xxx.trycloudflare.com/webhook
fields:        ["messages"]
verify_token:  <META_VERIFY_TOKEN du .env>
```

**Sans le MCP** — même chose en `curl` :

```bash
curl -X POST "https://graph.facebook.com/v25.0/$META_APP_ID/subscriptions" \
  -H "Authorization: Bearer $META_ACCESS_TOKEN" \
  -d "object=whatsapp_business_account" \
  -d "callback_url=https://xxx.trycloudflare.com/webhook" \
  -d "fields=messages" \
  -d "verify_token=$META_VERIFY_TOKEN"
```

Meta appelle immédiatement ton `GET /webhook` avec un `hub.challenge`. Le serveur
le renvoie tel quel — c'est déjà implémenté, tu n'as rien à coder.

**Vérification n°1** — dans les logs du serveur :

```
Webhook vérifié par Meta avec succès
```

**Vérification n°2** — l'abonnement existe (`devtools_webhook_list` →
`list_subscriptions`, ou `GET /{app_id}/subscriptions`) : le champ `messages`
doit apparaître avec ta `callback_url`.

> Si Meta répond `(#2200) callback verification failed` : le tunnel n'est pas
> joignable, ou le `verify_token` envoyé ne correspond pas à celui du `.env`.
> Ce sont les deux seules causes — vérifie-les dans cet ordre.

### 4 bis — Abonner le WABA à l'app ✅ automatique (piège classique)

**Il y a DEUX abonnements, pas un.** Celui du dessus relie ton *app* au webhook.
Il faut en plus relier ton *compte WhatsApp Business* (WABA) à l'app — sinon
l'abonnement s'affiche comme actif partout, et **aucun message n'arrive jamais**.

Cette seconde étape n'est pas faisable avec un token d'app : il faut le token
d'accès de l'étape 2 (permission `whatsapp_business_management`).

```bash
curl -X POST "https://graph.facebook.com/v25.0/$META_WABA_ID/subscribed_apps" \
  -H "Authorization: Bearer $META_ACCESS_TOKEN"
```

**Vérification** :

```bash
curl -s "https://graph.facebook.com/v25.0/$META_WABA_ID/subscribed_apps" \
  -H "Authorization: Bearer $META_ACCESS_TOKEN"
```

Ton app doit apparaître dans `data`. Une liste vide = les messages n'arriveront
pas, quoi que dise l'abonnement de l'étape 4.

> `(#200) You do not have permission to access this field` sur cet appel signifie
> que tu utilises un token d'app (`APP_ID|APP_SECRET`) au lieu du token d'accès.
> Les deux ne sont pas interchangeables.

---

## Étape 5 — Numéro destinataire 🔴 manuel

> 🔴 **Une minute.** Meta n'autorise l'envoi qu'à des numéros vérifiés tant que
> l'app est en mode développement, et la vérification passe par un code SMS.
>
> 1. WhatsApp → Configuration de l'API → champ **À** → **Gérer la liste**
> 2. Ajoute ton propre numéro de portable
> 3. Saisis le code reçu par SMS

---

## Étape 6 — Le vrai test ✅ + 🔴

> 🔴 **Écris « Bonjour » depuis ton téléphone** au numéro de test affiché dans
> la console Meta (« De »). Ajoute-le d'abord à tes contacts.

✅ **Tu vérifies**, dans les logs :

```
Message de tel_xxxxxxxx…78 : <9 caractères, contenu masqué> (19 restants…)
Claude claude-sonnet-5 — 1247 entrée / 89 sortie (~0.0050 $…)
Réponse envoyée à tel_xxxxxxxx…78
```

Les trois lignes dans cet ordre = **l'agent est en production**. Le numéro
apparaît masqué : c'est voulu, pas un bug.

| Symptôme | Cause | Correction |
|---|---|---|
| Aucun log | Webhook non reçu | Tunnel fermé, ou abonnement absent (étape 4) |
| `401 Signature invalide` | `META_APP_SECRET` faux | Recopie-le, sans espace ni retour à la ligne |
| Message reçu, pas de réponse | Clé Anthropic absente/invalide | Vérifie `ANTHROPIC_API_KEY` |
| `(#131030)` à l'envoi | Numéro non vérifié | Étape 5 |
| Réponse en double | Deux serveurs écoutent | Tue les processus en trop |

---

## Étape 7 — Token permanent (System User) 🔴 manuel

> C'est l'étape la plus technique de tout le parcours. Prends le temps de la
> guider écran par écran : c'est là que les gens abandonnent. Environ 5 minutes.

### Pourquoi

Le token de l'étape 2 expire au bout de 24 h. Un agent branché dessus tombera en
panne le lendemain, **silencieusement** : le serveur tourne, le webhook arrive,
mais chaque envoi échoue en `190`. Il faut un token qui n'expire jamais.

| Type de token | Durée de vie | Verdict |
|---|---|---|
| Temporaire (console WhatsApp) | 24 h | Pour tester tout de suite |
| Long-lived user token | 60 jours | Inutile : il expire quand même |
| **System User** | **Permanent** | ✅ Le seul valable en production |

> La Graph API peut générer un token System User
> (`POST /{app_id}/system_user_access_tokens`), mais l'appel exige de **déjà**
> détenir un token System User admin. Le premier passe donc obligatoirement par
> l'interface. Ne cherche pas à automatiser ce tour-là : c'est impossible.

### La procédure

> 🔴 **J'ai besoin de toi, environ 5 minutes. Suis exactement cet ordre.**
>
> **1. Ouvre les paramètres du portefeuille d'entreprise**
> https://business.facebook.com/settings/system-users
> (Meta redirige parfois vers `/latest/settings/system_users` — c'est le même écran.)
> Si plusieurs portefeuilles existent, **choisis celui qui contient ton compte
> WhatsApp Business**. Se tromper de portefeuille est l'erreur numéro un.
>
> **2. Crée l'utilisateur système**
> **Ajouter** → un nom au choix (par exemple « agent-whatsapp ») → rôle **Admin**
> → **Créer**.
>
> **3. Attribue les DEUX ressources** — c'est ici que ça casse le plus souvent
> Sélectionne l'utilisateur créé → **Ajouter des ressources**.
> Il en faut **deux**, pas une :
>
> - Onglet **Applications** → ton app (`lm-agent-kit-test`) → **Contrôle total**
> - Onglet **Comptes WhatsApp** → ton compte WhatsApp Business → **Contrôle total**
>
> Puis **Enregistrer les modifications**.
>
> N'oublie pas la seconde : avec seulement l'app, le token se génère sans erreur
> mais tout appel WhatsApp répondra `(#200) permission denied`. Rien n'indique la
> cause.
>
> **4. Génère le token**
> **Générer un nouveau token** → sélectionne ton app.
>
> ⚠️ **Règle l'expiration sur « Jamais »** avant de valider. Le menu propose
> « 60 jours » par défaut, et c'est le piège : le token obtenu est bien de type
> `SYSTEM_USER`, tout semble correct, mais il meurt dans deux mois. Seul
> `debug_token` révèle la différence (voir vérification 1).
>
> Coche ensuite ces **trois** permissions :
>
> - `whatsapp_business_messaging` — envoyer et recevoir les messages
> - `whatsapp_business_management` — gérer le compte et les abonnements
> - `business_management` — lire le portefeuille d'entreprise
>
> → **Générer un token**
>
> **5. Copie-le TOUT DE SUITE**
> Meta ne l'affichera plus jamais. Si tu fermes la fenêtre sans copier, il faut
> tout recommencer à partir de l'étape 4.
>
> Colle-le-moi : je le mets dans `.env` et nulle part ailleurs.

### Vérification ✅ — trois contrôles, dans cet ordre

**1. Le token est-il valide et permanent ?**

```bash
curl -s "https://graph.facebook.com/v25.0/debug_token?input_token=$META_ACCESS_TOKEN&access_token=$META_APP_ID%7C$META_APP_SECRET" \
  | python3 -m json.tool
```

Attendu : `"is_valid": true`, `"type": "SYSTEM_USER"`, et surtout
**`"expires_at": 0`** — le zéro signifie « n'expire jamais ».

Si `expires_at` porte une date, le token est bien un System User mais avec
l'expiration à 60 jours : retourne à l'étape 4 et choisis **« Jamais »**. C'est
l'erreur la plus sournoise du parcours, parce que tout fonctionne pendant deux
mois avant de tomber en panne sans prévenir.

Vérifie aussi les `scopes` : `whatsapp_business_messaging` et
`whatsapp_business_management` sont indispensables. `business_management` ne sert
qu'à lire le portefeuille — son absence n'empêche pas l'agent de fonctionner.

**2. Donne-t-il accès au numéro WhatsApp ?**

```bash
curl -s "https://graph.facebook.com/v25.0/$META_PHONE_NUMBER_ID?fields=display_phone_number,verified_name,quality_rating" \
  -H "Authorization: Bearer $META_ACCESS_TOKEN"
```

Un JSON avec `display_phone_number` = ✅. Un `(#200)` = la ressource « Comptes
WhatsApp » n'a pas été attribuée à l'étape 3.

**3. Le WABA est-il abonné à l'app ?** (voir 4 bis)

```bash
curl -X POST "https://graph.facebook.com/v25.0/$META_WABA_ID/subscribed_apps" \
  -H "Authorization: Bearer $META_ACCESS_TOKEN"
```

Attendu : `{"success": true}`.

### Quand ça ne marche pas

| Message | Cause réelle | Correction |
|---|---|---|
| `(#200) You do not have permission` | Ressource WhatsApp non attribuée | Étape 3, onglet **Comptes WhatsApp** |
| `expires_at` ≠ 0, type `SYSTEM_USER` | Expiration laissée à « 60 jours » | Étape 4, choisis **« Jamais »** |
| `expires_at` ≠ 0, autre type | Token utilisateur, pas System User | Reprends à l'étape 1 |
| `(#190) Invalid OAuth access token` | Copie tronquée ou espace parasite | Recopie sans retour à la ligne |
| Token absent des scopes attendus | Permissions non cochées | Étape 4, régénère |
| L'app n'apparaît pas dans la liste | Mauvais portefeuille d'entreprise | Étape 1, change de portefeuille |

✅ Une fois les trois contrôles passés : remplace `META_ACCESS_TOKEN` dans `.env`
et redémarre le serveur. `GET /` doit alors renvoyer `"statut":"ok"` avec le
numéro affiché — et non plus `"degrade"`.

## Étape 8 — Production

```bash
ENVIRONMENT=production
```

Trois conséquences, toutes voulues :

- La signature devient **obligatoire** : sans `META_APP_SECRET`, le serveur
  refuse de démarrer. Ne contourne pas.
- Le simulateur n'est plus monté (c'est un canal non authentifié).
- Passe à **PostgreSQL** : en SQLite, l'historique est effacé à chaque
  redéploiement.

Remplace le tunnel par une URL stable (Railway, Fly, VPS) et **réabonne le
webhook** sur cette nouvelle URL — l'étape 4, à refaire.

Rappelle enfin la **fenêtre de 24 h** : hors de ce délai après le dernier message
du client, seul un template approuvé par Meta peut être envoyé. Comme l'agent
répond toujours à quelqu'un qui vient d'écrire, ça ne se pose jamais en pratique.

---

## Note — usernames WhatsApp et BSUID

Depuis juillet 2026, un client peut écrire sans partager son numéro : Meta livre
alors un **BSUID** dans `from_user_id` et laisse `from` **vide**. Le kit ingère
déjà les deux (`agent/providers/meta.py`).

Deux choses à savoir :

- Meta ne fournit le numéro que **30 jours** après le dernier échange. Passé ce
  délai, un client existant réapparaît comme inconnu si l'on ne s'appuie que sur
  le numéro — d'où l'usage du BSUID comme identifiant de conversation.
- Un **username business** ne dispense pas d'un numéro de téléphone : il se
  réserve *après*, et il ne masque pas le numéro de l'entreprise.
