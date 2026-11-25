# M365 Service Health pour Home Assistant

*[English version](README.md)*

Surveille **l'état de santé des services Microsoft 365 de ton tenant** (Exchange Online,
Teams, SharePoint, Intune…) dans Home Assistant, grâce à
l'[API des communications de service](https://learn.microsoft.com/graph/api/resources/service-communications-api-overview)
de Microsoft Graph.

- Un capteur par service : `operational` (opérationnel), `advisory` (avis) ou `incident`
- Des compteurs d'incidents et d'avis en cours, et un capteur « problème » global
- Un événement à chaque problème nouveau, mis à jour ou résolu, prêt pour les
  notifications (un [blueprint](#notifications) est fourni)
- Configuration dans l'interface, réauthentification quand le secret expire, diagnostics
- Aucune dépendance Python supplémentaire, accès en lecture seule

> Projet communautaire, sans lien avec Microsoft ni approuvé par Microsoft.

## Pourquoi ne pas reprendre simplement le statut de Microsoft ?

Le statut global de Microsoft est souvent trompeur : un service passe en
`serviceDegradation` dès qu'un simple *avis* est ouvert, et parfois même quand tous ses
problèmes sont résolus. Cette intégration calcule l'état à partir des problèmes
**encore ouverts** :

| État | Signification |
|---|---|
| `operational` | Aucun problème ouvert pour ce service |
| `advisory` | Seulement des avis (impact limité, souvent quelques utilisateurs ou une fonction) |
| `incident` | Au moins un incident (impact visible pour les utilisateurs) |

Le statut brut de Microsoft reste disponible dans l'attribut `microsoft_status`.

## Prérequis : une inscription d'application Entra ID

Les étapes 2 et 3 demandent un administrateur pouvant accorder le consentement
(par exemple *Administrateur de rôle privilégié* ou *Administrateur général*).

1. Dans le [centre d'administration Microsoft Entra](https://entra.microsoft.com) :
   **Applications → Inscriptions d'applications → Nouvelle inscription**. Donne-lui un nom
   (par exemple `HomeAssistant-M365-ServiceHealth`), garde *Locataire unique*, sans URI de redirection.
2. **Autorisations de l'API → Ajouter une autorisation → Microsoft Graph → Autorisations
   d'application**, coche **`ServiceHealth.Read.All`**, puis **Ajouter des autorisations**.
   Ce doit être une autorisation *d'application*, pas *déléguée*.
3. Clique sur **Accorder un consentement d'administrateur pour &lt;ton tenant&gt;** :
   l'état doit passer au vert.
4. **Certificats et secrets → Nouveau secret client**. Copie tout de suite la **Valeur**
   du secret (affichée une seule fois ; l'*ID du secret* ne sert à rien ici). Note la date d'expiration.
5. Sur la page **Vue d'ensemble**, copie l'**ID d'application (client)** et
   l'**ID de l'annuaire (locataire)**.

Si plusieurs personnes utilisent l'intégration, crée **un secret par personne** dans la
même inscription : chacun peut être révoqué sans toucher aux autres.

## Installation

### HACS (recommandé)

1. Dans HACS, ouvre le menu (⋮) → **Dépôts personnalisés**.
2. Ajoute `https://github.com/red248/ha-m365-service-health` avec le type **Intégration**.
3. Cherche **M365 Service Health**, télécharge-la, puis redémarre Home Assistant.

### Manuelle

Copie `custom_components/m365_service_health` dans le dossier `custom_components` de ta
configuration Home Assistant, puis redémarre Home Assistant.

## Configuration

**Paramètres → Appareils et services → Ajouter une intégration → M365 Service Health**,
puis saisis :

| Champ | Valeur |
|---|---|
| Nom | Nom de l'appareil, `Microsoft 365` par défaut (utile avec plusieurs tenants) |
| ID du tenant | ID de l'annuaire (locataire), ou un domaine comme `contoso.onmicrosoft.com` |
| ID d'application (client) | Page Vue d'ensemble de l'inscription |
| Secret client | La **Valeur** du secret |

Options (bouton **Configurer**) : intervalle d'interrogation (5 minutes par défaut, de 2 à 60)
et services à ignorer. Les services ajoutés plus tard par Microsoft sont suivis automatiquement.

Quand le secret expire, Home Assistant affiche une notification **Reconfigurer** : crée un
nouveau secret dans Entra ID et saisis-le à cet endroit. Pour changer d'inscription
d'application, utilise **Reconfigurer** dans le menu de l'intégration.

## Entités

Avec le nom par défaut `Microsoft 365` :

| Entité | Description |
|---|---|
| `sensor.microsoft_365_<service>` | Une par service, par exemple `sensor.microsoft_365_exchange_online` : `operational` / `advisory` / `incident`. Attributs : `microsoft_status`, `incidents`, `advisories` (id, titre, statut, début, lien vers le centre d'administration) |
| `sensor.microsoft_365_incidents_en_cours` | Nombre d'incidents en cours. Attribut `issues` |
| `sensor.microsoft_365_avis_en_cours` | Nombre d'avis en cours. Attribut `issues` |
| `binary_sensor.microsoft_365_incident` | `on` tant qu'au moins un incident est en cours (classe *problème*) |

Les identifiants d'entités suivent la langue de Home Assistant au moment de l'ajout de
l'intégration (en anglais : `..._active_incidents` et `..._active_advisories`). Seuls les
services suivis sont comptés. Les attributs de type liste ne
sont pas enregistrés dans l'historique, pour ne pas alourdir la base de données.

Les liens `url` ouvrent le problème dans le centre d'administration Microsoft 365 : il faut un
compte avec un rôle d'administration qui donne accès à l'état des services (par exemple *Lecteur
général* ou *Administrateur du support de service*). Un compte utilisateur normal est refusé.

## Notifications

L'intégration envoie l'événement `m365_service_health_issue` quand un problème apparaît
(`new`), change (`updated`, par exemple un avis requalifié en incident) ou disparaît des
problèmes ouverts (`resolved`). Aucun événement n'est envoyé pour les problèmes déjà
ouverts au démarrage de Home Assistant.

Données de l'événement : `action`, `id`, `service`, `title`, `classification`, `status`,
`feature`, `impact_description`, `start`, `last_modified`, `url`, `tenant`, `config_entry_id`.

### Blueprint

[![Importer le blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fred248%2Fha-m365-service-health%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fm365_service_health_notify.yaml)

Choisis les types de problèmes, les changements qui t'intéressent et les actions à lancer.

### Exemple d'automatisation

```yaml
triggers:
  - trigger: event
    event_type: m365_service_health_issue
    event_data:
      action: new
      classification: incident
actions:
  - action: notify.mobile_app_mon_telephone
    data:
      title: "Incident Microsoft 365 : {{ trigger.event.data.service }}"
      message: "{{ trigger.event.data.title }}"
      data:
        url: "{{ trigger.event.data.url }}"
```

### Exemple de carte

```yaml
type: markdown
title: Microsoft 365
content: >-
  {% set issues = state_attr('sensor.microsoft_365_incidents_en_cours', 'issues') or [] %}
  {% for i in issues %}
  - **{{ i.service }}** : [{{ i.title }}]({{ i.url }})
  {% else %}
  Aucun incident en cours.
  {% endfor %}
```

## Dépannage

| Erreur | À vérifier |
|---|---|
| Secret client invalide | Copier la **Valeur** du secret, pas son *ID* |
| Secret client expiré | Créer un nouveau secret, puis passer par la notification de réauthentification |
| Permission manquante | `ServiceHealth.Read.All` doit être une autorisation *d'application* avec consentement administrateur |
| Aucune application avec cet ID client | Mauvais ID client, ou application inscrite dans un autre tenant |
| Bloqué par l'accès conditionnel | Une stratégie d'accès conditionnel pour les identités de charge de travail bloque l'application |

Pour plus de détails, active les journaux de débogage depuis la page de l'intégration,
ou télécharge les diagnostics (les identifiants et l'ID du tenant en sont retirés).

## Sécurité

- L'application n'a que `ServiceHealth.Read.All` : elle peut lire l'état des services du
  tenant, rien d'autre (ni boîtes mail, ni données utilisateurs).
- Home Assistant stocke le secret dans sa configuration (`.storage`), qui figure aussi
  dans ses sauvegardes : protège-les en conséquence.
- Préfère des secrets de courte durée et un secret par personne.

## Limites

- Le centre de messages (changements prévus, `ServiceMessage.Read.All`) n'est pas encore pris en charge.
- Microsoft met à jour ses problèmes toutes les quelques minutes : interroger plus souvent
  que toutes les 2 minutes n'apporte rien.

## Licence

[MIT](LICENSE)
