# 📝 Bilan des modifications : Intégration Claude Code & g4f-bridge

Ce document résume le travail accompli pour résoudre les problèmes d'affichage et de sélection des modèles dans Claude Code via `g4f-bridge`.

## 🎯 Objectif global
Permettre à l'utilisateur d'utiliser Claude Code de manière transparente avec un sous-ensemble restreint et priorisé de modèles IA gratuits (Kimi, Claude, Grok, GPT-5, Qwen, Gemini), sans être bloqué sur un seul modèle (comme Gemini) et sans être pollué par des centaines de modèles inutiles (Nemotron, Gemma, etc.).

---

## 🛠️ Modifications réalisées

### 1. Filtrage prioritaire automatique (`src/bridge/main.py`)
- **Problème :** Par défaut, `g4f-bridge` chargeait plus de 370 modèles, inondant Claude Code.
- **Solution :** Création d'une fonction `priority_model_selection()` qui filtre automatiquement les 6 familles requises et garde uniquement les modèles les plus populaires. Gemini a été plafonné à 3 variantes maximum pour ne pas écraser les autres.
- **Amélioration :** Ce filtrage prioritaire est désormais **le comportement par défaut** si tu lances `g4f-bridge` sans argument. (Un argument `--priority` a également été ajouté pour un usage explicite).

### 2. Purge de la configuration Claude Code (`src/configs/claude_code.py`)
- **Problème :** Claude Code restait bloqué sur `claude-gemini-3.5-flash-lite` car ce modèle avait été inscrit en dur (hardcodé) comme `globalModel` lors d'une ancienne session.
- **Solution :** Le script de configuration automatique de `g4f-bridge` purge désormais activement les clés `globalModel`, `model` et `modelSettings` dans ton fichier `~/.claude/settings.json` à chaque lancement. Claude Code te demandera toujours de choisir le modèle parmi la liste propre.

### 3. Restauration du "Hack" de l'alias (`src/bridge/main.py`)
- **Problème :** Dans une tentative de nettoyer l'affichage, les alias commençant par `claude-` pour les modèles Kimi, Grok, etc., avaient été retirés. Résultat : Claude Code (qui censure tout modèle ne ressemblant pas à un produit Anthropic) les masquait.
- **Solution :** Restauration de la génération de l'alias `claude-*` pour **tous** les modèles. C'est la condition *sine qua non* pour que Claude Code les affiche.

### 4. Documentation des modèles premium EAON (`src/bridge/models.py`)
- **Problème :** Les modèles très attendus (Kimi k2.6, Grok 4.5, Qwen 3.8 Max) nécessitent une clé EAON, ce qui n'était pas clair.
- **Solution :** Ajout de commentaires clairs dans la liste statique des modèles expliquant comment les débloquer via la commande `g4f-bridge --keys`.

---

## 🚀 Perspectives d'amélioration futures

1. **Sauvegarde de la configuration (`settings.json` du bridge) :**
   Actuellement, la liste des modèles actifs (`ACTIVE_MODELS`) est conservée en mémoire vive (RAM). Il serait intéressant d'implémenter l'écriture de ces préférences dans `~/.g4f-bridge/settings.json` pour pouvoir conserver des sélections personnalisées très fines d'une session à l'autre sans dépendre du code en dur.

2. **Acquisition de la clé EAON :**
   Le passage à la vitesse supérieure nécessitera de récupérer une clé sur `https://api.eaon.dev` et de l'ajouter via `g4f-bridge --keys`. Cela débloquera l'accès garanti aux versions *Max* et *Pro* de Qwen, Grok et Kimi.

3. **Intégration TUI complète :**
   Continuer le développement de l'interface graphique en ligne de commande (Terminal User Interface via Node.js mentionnée dans le projet) pour permettre à l'utilisateur de cocher/décocher ses modèles en temps réel sans avoir à relancer le serveur.

4. **Tests de viabilité automatisés améliorés (`-t`) :**
   Le "Stress Test" actuel peut parfois exclure des modèles un peu lents mais fonctionnels (timeout). Il pourrait être affiné (allongement du délai ou tests asynchrones) pour garantir qu'aucun bon modèle (comme un Kimi ou un Grok un peu chargé) ne soit éliminé à tort au démarrage du bridge.
