# Documents juridiques

> **Ces documents doivent être relus par un juriste ou un avocat.**
>
> Les gabarits du kit couvrent ce que Meta exige et ce que le RGPD impose dans
> le cas courant. Ils ne sont pas un avis juridique, et personne ici ne connaît
> votre activité, vos contrats, ni les traitements que vous menez par ailleurs.
>
> Un document généré et jamais relu vous expose **plus** qu'il ne vous protège :
> il établit que vous aviez conscience de l'obligation. Comptez une heure de
> relecture professionnelle. Tant que `revue_juridique.effectuee` vaut `false`,
> chaque page publiée affiche un bandeau d'avertissement — visible de vos
> clients comme d'un examinateur Meta.

---

## 1. Pourquoi l'agent publie ses propres documents

Meta réclame une URL de politique de confidentialité et une URL d'instructions
de suppression. L'agent ayant déjà une adresse HTTPS publique, il les sert
lui-même sur `/legal`.

Ce n'est pas de la commodité. Une page hébergée ailleurs se désynchronise
toujours de la configuration réelle : vous passez `RETENTION_JOURS` de 90 à 30,
et votre politique continue d'annoncer 90 jours pendant deux ans. **Une
politique inexacte est pire qu'une absence de politique** — elle démontre le
manquement au lieu de le couvrir.

Le générateur lit donc dans votre `.env` ce que le kit sait déjà : durée de
conservation, fournisseur d'IA, mode de transparence, journalisation du contenu,
services de traitement des fichiers. Ces valeurs ne sont jamais redemandées, et
les documents suivent la configuration.

> **Ce fichier est la référence de l'assistant d'installation, pas votre marche
> à suivre.** C'est lui qui mène l'entretien juridique, remplit
> `config/juridique.yaml`, vérifie son travail et pose les URL dans Meta — voir
> `AGENTS.md`, étape 2 ter. Vous n'intervenez que sur deux points : les quatre
> informations que nul ne peut deviner (raison sociale, immatriculation,
> représentant légal, e-mail RGPD), et **la relecture par un juriste**.
>
> Ce qui suit détaille le contenu, les écarts entre pays et les arbitrages —
> utile pour comprendre ce que l'assistant produit, ou pour reprendre la main.

## 2. Mise en place

```bash
cp config/juridique.exemple.yaml config/juridique.yaml
```

Remplissez-le — une quinzaine de champs — puis redéployez. Les pages
apparaissent aussitôt :

| Adresse | Document |
|---|---|
| `/legal/confidentialite` | Politique de confidentialité |
| `/legal/cgu` | Conditions d'utilisation |
| `/legal/mentions` | Mentions légales |
| `/legal/suppression` | Suppression des données |
| `/legal/ia` | Information sur l'usage de l'IA |
| `/legal/sous-traitance` | Annexe sous-traitance *(mode agence uniquement)* |

Sans `config/juridique.yaml`, les routes renvoient 404 : le kit préfère ne rien
publier plutôt que de publier un gabarit non rempli.

Comme le reste de `config/`, ce fichier est exclu du dépôt public.

### Les deux modes

`mode: direct` — l'entreprise déploie son propre agent. Elle est seule
responsable de traitement. Cinq documents.

`mode: agence` — un intégrateur déploie pour un client. Le client reste
responsable de traitement, l'intégrateur devient sous-traitant, et une annexe
au titre de l'article 28 du RGPD est générée en plus. **Elle documente le
contenu attendu d'un contrat de sous-traitance ; elle ne le remplace pas.**

## 3. Où coller les URL dans Meta

App Dashboard → **Paramètres → Général** :

| Champ Meta | Valeur |
|---|---|
| Privacy Policy URL | `https://votre-agent/legal/confidentialite` |
| Terms of Service URL | `https://votre-agent/legal/cgu` |
| User Data Deletion | Choisir **Data Deletion Instructions URL** → `https://votre-agent/legal/suppression` |
| Data Protection Officer | L'adresse de `protection_donnees.email` |
| App Icon, App Purpose, Catégorie | Requis pour passer en mode **Live** |

Sur la suppression des données, Meta accepte **soit** un callback signé, **soit**
une URL d'instructions. Le callback est conçu pour Facebook Login et suppose un
`user_id` d'app : pour un agent WhatsApp, l'URL d'instructions est la bonne
route, et elle est explicitement admise.

Meta impose par ailleurs que la politique de confidentialité **explique
elle-même** comment demander la suppression. Le gabarit le fait, et renvoie vers
la page dédiée.

### Vérifier après coup

```bash
curl -sI https://votre-agent/legal/confidentialite | head -1
```

Un examinateur ouvrira réellement ces adresses. Une URL qui renvoie 404 fait
échouer l'App Review, sans autre explication qu'un refus.

## 4. L'opt-in, souvent négligé

Depuis la mise à jour de novembre 2024 de la politique de messagerie, un
consentement préalable est exigé avant d'écrire à quelqu'un. Il doit indiquer
clairement **qu'on s'inscrit pour recevoir des messages** et **de quelle
entreprise**. Les méthodes admises : formulaire web, SMS, serveur vocal, papier.

La distinction qui compte :

- **Le client vous écrit le premier.** Vous pouvez répondre. C'est le cas normal
  d'un agent de service, et c'est ce que décrit le gabarit.
- **Vous écrivez le premier.** Il faut un opt-in explicite, tracé, et daté — y
  compris pour un simple rappel de rendez-vous.

Documentez la méthode dans `traitement.opt_in`. En cas de contestation, c'est à
vous de prouver le consentement.

## 5. Ce qui change selon le pays

Le socle des gabarits est **France / Union européenne**. Le RGPD s'applique
uniformément dans l'UE ; les écarts portent surtout sur les mentions légales,
l'autorité de contrôle et quelques obligations locales.

| Zone | Ce qui change | À ajuster |
|---|---|---|
| **France** | LCEN : les mentions légales sont obligatoires et doivent nommer l'hébergeur. Loi Chatel pour la vente à distance. | Socle du kit, rien à faire |
| **Belgique, Luxembourg** | RGPD identique. Autorité : APD (BE), CNPD (LU). Mentions légales exigées par le droit de la consommation. | `autorite_controle`, `tribunal` |
| **Allemagne** | *Impressum* plus exigeant que les mentions françaises : registre du commerce, numéro de TVA, autorité de tutelle si profession réglementée. Amendes fréquentes sur ce point. | Faire relire l'Impressum localement |
| **Espagne, Italie** | RGPD + loi locale (LSSI-CE en Espagne). Autorités : AEPD, Garante. | `autorite_controle`, `tribunal` |
| **Royaume-Uni** | **UK GDPR** post-Brexit : structure identique, autorité différente (ICO), et le transfert UE↔UK repose sur une décision d'adéquation à surveiller. | `autorite_controle: ICO`, `droit_applicable` |
| **Suisse** | **nLPD** (révisée, en vigueur depuis sept. 2023) : proche du RGPD mais pas identique. Pas de registre obligatoire pour les PME de moins de 250 salariés. Autorité : PFPDT. Le RGPD s'applique **en plus** si vous visez des clients de l'UE. | Faire relire par un conseil suisse |
| **Québec** | **Loi 25** : la plus exigeante d'Amérique du Nord. Responsable de la protection des renseignements personnels obligatoire, évaluation des facteurs relatifs à la vie privée, et **consentement exprès** pour les usages secondaires. Autorité : CAI. | Gabarits à refondre, pas seulement à ajuster |
| **Reste du Canada** | LPRPDE/PIPEDA : plus souple, consentement implicite souvent admis. Autorité : Commissariat à la protection de la vie privée. | `autorite_controle` |
| **États-Unis** | Pas de loi fédérale. **CCPA/CPRA** en Californie et une vingtaine d'équivalents d'État, chacun avec ses seuils. Les mentions « Do Not Sell or Share » et les droits d'opt-out sont propres aux États-Unis et absents des gabarits. **Le TCPA sanctionne lourdement les messages non sollicités.** | Ne pas réutiliser les gabarits tels quels |
| **Brésil** | **LGPD**, calquée sur le RGPD. Autorité : ANPD. | `autorite_controle`, traduction |

**La règle qui prime sur ce tableau :** c'est la localisation de **vos clients**
qui décide, pas la vôtre. Une entreprise suisse qui répond à des clients
français applique le RGPD. Une entreprise française qui vise des Californiens
doit regarder la CCPA.

### L'AI Act européen

L'article 50 impose d'informer une personne qu'elle interagit avec un système
d'IA, depuis le **2 août 2026**. Le kit y répond par `MODE_TRANSPARENCE` et par
la page `/legal/ia`.

Trois réglages, et le troisième dispense du marquage :

- `discrete` — mention en fin de premier message *(recommandé)*
- `explicite` — encart en tête du premier message
- `validation` — aucune réponse ne part sans relecture humaine

Un agent qui renseigne et prend des demandes n'est **pas** un système à haut
risque au sens de l'AI Act. Il le deviendrait s'il décidait seul d'un accès au
crédit, à l'emploi ou à un service essentiel.

## 6. Faut-il passer par TermsFeed ou un équivalent ?

Les générateurs en ligne — TermsFeed, Iubenda, Termly — produisent des
documents corrects pour un site web classique. Trois questions décident.

**Vous voulez un document pour un site vitrine ou une boutique en ligne**, avec
cookies, analytics, paiement, comptes utilisateurs. → **Un générateur en ligne
est adapté.** Le kit ne couvre pas ces traitements ; il ne parle que de l'agent
WhatsApp.

**Vous voulez couvrir l'agent WhatsApp uniquement.** → **Les gabarits du kit
sont plus précis.** Un générateur générique ne connaît ni votre durée de
conservation réelle, ni le fournisseur d'IA que vous avez choisi, ni le fait que
vos numéros sont hachés dans les journaux. Il produira des formulations vagues
là où le kit met des faits vérifiables — et le vague se retourne contre vous en
cas de contrôle.

**Vous opérez hors d'Europe**, notamment aux États-Unis ou au Québec. → **Un
générateur spécialisé est un bon complément**, parce que les gabarits du kit
n'intègrent ni les mentions CCPA ni les exigences de la loi 25.

Le point sur lequel aucun générateur ne vous aidera : la **mise à jour**. Un
document acheté une fois vieillit dès que votre configuration change. Ceux du
kit se régénèrent à chaque déploiement à partir de la configuration en vigueur.

Un abonnement Iubenda ou Termly coûte quelques dizaines d'euros par an. Une
heure d'avocat coûte davantage et vaut beaucoup plus. **Si vous ne devez faire
qu'une chose, faites relire.**

## 7. Ce que le kit fait déjà pour vous

Ces mesures existent dans le code, et les documents s'appuient dessus. Les
citer n'est pas cosmétique : l'article 32 du RGPD impose des mesures
« appropriées », et pouvoir les nommer est ce qui distingue une conformité réelle
d'une déclaration d'intention.

- **Signature des webhooks obligatoire** en production — aucun message ne peut
  être injecté par un tiers.
- **Numéros masqués dans les journaux**, hachés avec un sel. Le contenu des
  messages n'y est jamais écrit.
- **Purge automatique** après `RETENTION_JOURS`, et effacement à la demande.
- **Aucun intermédiaire** : connexion directe à la Cloud API. Pas de
  sous-traitant supplémentaire à encadrer.
- **Plafond de dépense et limite de débit** par correspondant.
- **Transparence IA** paramétrable, appliquée à chaque conversation.

## 7 bis. Le bandeau, et comment le retirer

Tant qu'aucune décision n'est prise, chaque page publiée porte un avertissement
« Document non relu par un professionnel du droit ». **Vos clients le voient.**

Ce n'est pas une punition, c'est un garde-fou : il empêche qu'un document
juridique sorte sans que personne ait tranché. Mais une fois la décision prise,
l'afficher ne protège plus personne — sur les CGU d'un artisan, il inquiète
sans informer, et signale une faiblesse à qui la cherche.

**Trois états, deux façons de retirer le bandeau.**

| État | Bandeau | Ce que ça veut dire |
|---|:-:|---|
| Rien de renseigné *(défaut)* | affiché | Personne n'a tranché |
| `revue_juridique.effectuee: true` | retiré | Un juriste a relu |
| `publication_assumee.acceptee: true` | retiré | Vous publiez en connaissance de cause |

Le second chemin est légitime et fréquent. Ce que vous acceptez :

- Ces textes sont des gabarits. Ils n'ont pas été écrits pour **votre** activité,
  vos contrats, ni les traitements que vous menez par ailleurs.
- Une clause inexacte ou inopposable ne vous protège pas : elle décore. Vous
  découvrirez laquelle au moment où vous en auriez eu besoin.
- Publier un document juridique vous engage sur son contenu. En cas de litige,
  personne ne demandera qui l'a rédigé.

La décision est **datée et nommée** dans `config/juridique.yaml`, et l'agent la
rappelle à chaque démarrage dans ses journaux. Elle n'est pas cachée : elle
n'est simplement plus affichée à vos clients.

```yaml
revue_juridique:
  effectuee: false
  publication_assumee:
    acceptee: true
    par: "Marie Dupont, gérante"
    date: "2026-08-24"
```

L'assistant d'installation pose la question (`AGENTS.md`, Q6). Il a consigne de
ne jamais cocher cette case à votre place : retirer l'avertissement est une
décision de dirigeant, pas un réglage technique.

## 8. Après la mise en ligne

- Passez `revue_juridique.effectuee` à `true` **une fois la relecture faite**,
  avec le nom du relecteur et la date. Le bandeau disparaît alors des pages.
- Mettez à jour `publication.derniere_revision` à chaque modification.
- Rejouez la vérification des URL dans Meta après tout changement de domaine.
- Reprenez les documents si vous changez de fournisseur d'IA, d'hébergeur, ou
  de durée de conservation : le générateur suit la configuration, mais les
  finalités et la base légale, elles, restent de votre ressort.
