# Journal des versions

Toutes les évolutions notables d'AgentKit FR.

Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/), et les
numéros de version suivent le [versionnage sémantique](https://semver.org/lang/fr/).

Ce dépôt est cloné par des personnes qui l'installeront des mois après sa
publication. Ce fichier existe pour qu'elles sachent ce qu'elles récupèrent, et
pour qu'une mise à jour ne soit jamais une découverte.

## [Non publié]

Rien pour l'instant.

## [1.0.0] — 2026-08-28

Première version stable. L'agent tourne en production chez un premier client
depuis le 22 août 2026.

### Ce que fait l'agent

- Répond aux messages WhatsApp via la **Cloud API de Meta**, webhook signé en
  HMAC-SHA256, déduplication des événements rejoués (Meta réessaie 7 fois).
- Réponse HTTP immédiate puis traitement en tâche de fond : le fournisseur
  attend un `2xx` en 5 secondes, un appel au modèle prend plus longtemps.
- **Quatre outils réellement exécutés** — recherche dans les documents du
  métier, vérification de délai en code, enregistrement d'une demande,
  transfert à un humain.
- **Réception des fichiers** : notes vocales transcrites, photos et vidéos
  analysées, PDF extraits — avec OCR par le modèle de vision quand le PDF est
  un scan. Fournisseur et modèle réglables par type de fichier.
- **Escalade vers un humain** quand l'agent ne sait pas, quand la conversion
  d'un fichier échoue, ou quand aucun fournisseur n'est configuré pour ce type.
- **Multi-fournisseurs de modèle** : Anthropic et tous les compatibles OpenAI,
  Gemini et OpenRouter compris.

### Console d'administration

- Comptes nommés, mot de passe haché en scrypt, session signée, limitation des
  tentatives de connexion, récupération d'accès en ligne de commande.
- Conversations qui se rafraîchissent seules, fichiers reçus écoutables et
  visibles sur place avec leur transcription.
- Édition du prompt, des documents métier, des consignes ponctuelles et des
  messages types sans toucher au code.
- Reprise de la main sur une conversation : l'agent se tait, l'humain répond.

### Conformité

- **Six documents juridiques** générés depuis la configuration de l'entreprise :
  politique de confidentialité, CGU, mentions légales, suppression des données,
  transparence IA, annexe de sous-traitance. Servis par l'agent lui-même.
- Information de l'utilisateur qu'il parle à une IA (**AI Act, article 50**).
- Purge automatique de l'historique selon la durée de conservation choisie,
  téléphone masqué dans les journaux, contenu des messages jamais journalisé
  par défaut.

### Garde-fous

- **Plafond de dépense journalier**, persistant en base : il survit aux
  redéploiements.
- Limitation de débit par numéro, liste blanche pour la phase de test,
  plafond de tours d'outils.
- Revue de configuration au démarrage : ce qui manque ou ce qui est dangereux
  est dit explicitement dans les journaux et dans la console.

### Mise en ligne

- `Dockerfile` et `docker-compose.yaml` prêts pour Railway, Coolify ou un VPS.
- PostgreSQL en production, SQLite en local, migration de schéma automatique.
- L'agent n'annonce pas sa pile technique dans ses en-têtes HTTP.

### Vérification

326 tests, dont un parcours complet du webhook jusqu'à l'écran de la console,
une campagne adversariale, et une suite exécutée contre un vrai PostgreSQL.
