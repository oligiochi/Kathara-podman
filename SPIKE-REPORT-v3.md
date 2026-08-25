# SPIKE-REPORT-v3 — podman-py native backend

Companion to the v3.0 roadmap (`docs` conversation, Parte 4). Re-validates the P1–P9 spikes
against `podman-py` **5.8.0** (the latest release at the time of writing) and the actual Kathará
backend code now committed under `src/Kathara/manager/podman/`.

**Method note:** this environment has no reachable Podman/libpod service (no daemon, no socket),
so these findings come from direct inspection of the installed `podman-py` source
(`podman.domain.containers_create.CreateMixin._render_payload`, `podman.domain.containers.Container`,
`podman.domain.networks_manager.NetworksManager`, `podman.domain.networks.Network`) plus unit tests
that exercise the Kathará-side code against mocked podman-py objects. It is **not** a substitute for
running the backend against a live `podman.socket`, which is the natural next step before this code
is considered release-ready. Every finding below is traceable to a specific piece of podman-py source,
not to observed runtime behavior.

| Spike | Esito | Note |
|---|---|---|
| **P1** connessione | PASS | `PodmanClient(base_url=...)` + `.ping()` confermati; `.ping()` non solleva da solo su fallimento — la vera eccezione arriva dalla chiamata HTTP sottostante come `podman.errors.APIError` (non `PodmanError`), quindi `check_podman_status` deve intercettare entrambe (vedi §Findings 1). |
| **P2** mappatura kwargs `containers.create` | PASS con eccezioni | Vedi §Findings 2–4: `nano_cpus` è **silenziosamente ignorato**, `networking_config` non esiste, `network_mode` e `networks` sono mutuamente esclusivi a livello di chiamata. |
| **P3** exec batch + wording crun | PASS | `Container.exec_run()` esiste ma è un aggregato che non restituisce l'exec id in modalità stream — insufficiente per Kathará (serve poi per interrogare l'exit code / fare resize). Confermato che l'unico modo è passare dalle route HTTP `/containers/{id}/exec` e `/exec/{id}/*` direttamente, come fa `podman_exec.py`. |
| **P4** TTY hijack | PASS | Nessuna API pubblica in podman-py 5.8.0 (verificato: nessun metodo con `exec` in `podman.api.client.APIClient`). Lo shim in `podman_exec.py` è l'unica via. |
| **P5** archive put/get | PASS | `Container.put_archive(path, data)` / `Container.get_archive(path)` presenti e con la stessa firma logica di docker-py. |
| **P6** network/IPAM | PASS | `NetworksManager.create(driver=, internal=, labels=, ipam=IPAMConfig(driver=...))` mappa `ipam.Driver` su `ipam_options.driver` lato payload: `ipam_driver="none"` è quindi realmente raggiungibile nativamente (non lo era sotto compat). |
| **P7** forma `networks` alla create | PASS con gap noto | `networks` è passato as-is al payload libpod (nessuna traduzione): conferma che deve essere `{network_name: {...}}`. Non è stato possibile validare contro un servizio reale se le sotto-chiavi per-network (es. `static_mac`) sono effettivamente `static_mac`/`interface_name`; il codice usa invece il campo **container-level** `mac_address` (mappato con certezza su `static_mac` da `_render_payload`) per la prima interfaccia, e logga un warning esplicito per MAC statici su interfacce collegate a runtime (limite dichiarato, non silenzioso). |
| **P9** schema `container.stats()` | PASS (assunto) | `Container.stats(stream=True, decode=True)` esiste con la stessa firma di docker-py; lo schema del payload (`cpu_stats`, `memory_stats`, `pids_stats`, `networks`) non è verificabile senza un servizio live, ma la route libpod è storicamente mantenuta compatibile con quella Docker per motivi di tooling (ctop, lazydocker, ecc.). `PodmanMachineStats` include guardie difensive (`.get(...)`) dove lo schema Docker non è garantito identico. |

## Findings (con impatto sul codice)

1. **`ping()` non solleva da solo.** `SystemManager.ping()` fa `self.client.head("/_ping"); return response.ok`. Se il socket non esiste, l'eccezione arriva PRIMA, dalla libreria HTTP sottostante, come `podman.errors.APIError` (non un semplice `requests.exceptions.ConnectionError`, e non `podman.errors.PodmanError`). `check_podman_status` in `PodmanManager.py` intercetta esplicitamente `(RequestsConnectionError, PodmanError, APIError)` — confermato con un test che il caso "nessun demone" produce un `ContainerEngineConnectionError` pulito.

2. **`nano_cpus` è ignorato da podman-py.** `_render_payload` lo elenca esplicitamente tra le chiavi scartate (`# Ignore these keywords`). `PodmanMachine.create()` non lo usa: converte invece la frazione di CPU Kathará in `cpu_period`/`cpu_quota` (periodo fisso 100000µs), che *sono* mappati.

3. **`networking_config` non esiste.** Non è tra le chiavi gestite da `_render_payload`: passarlo solleverebbe `TypeError: Unknown keyword argument(s)`. `PodmanMachine.create()` usa `networks={network_name: {}}` per la prima interfaccia, mai `networking_config`.

4. **`network_mode` e `networks` non sono semplicemente ignorabili se `None`.** La gran parte delle chiavi di `_render_payload` usa un default `None` sia che la chiave sia assente sia che sia esplicitamente `None` — ma `network_mode` è gestito con un `if "network_mode" in args:` che testa la *presenza* della chiave, non il suo valore: passare `network_mode=None` esplicitamente causa un crash (`None.split(":")`). Lo stesso vale per `volumes`/`mounts`/`ports` (default non-`None`: `{}`/`[]`/`{}`). `PodmanMachine.create()` costruisce i kwargs condizionalmente proprio per questo (vedi il commento inline "mutually exclusive... passing the key at all, even as None, is enough").

5. **Volumi anonimi.** Non verificabile senza demone live, ma la mitigazione (mount reale o tmpfs su `/hosthome` e `/shared`) è implementata in `PodmanMachine.create()` indipendentemente dal risultato dello spike, seguendo la scoperta già documentata nel roadmap.

6. **`get_registry_data` è un finto endpoint.** In podman-py 5.8.0, `ImagesManager.get_registry_data()` non contatta affatto il registry: richiama semplicemente `self.get(name)` (l'immagine locale) e la incapsula in un oggetto `RegistryData`. Confermato leggendo il sorgente: non è un problema di trasporto, è strutturale. `PodmanImage.check_for_updates()` è quindi un no-op documentato, non un tentativo silenzioso e fallace.

## Cosa resta da validare contro un servizio Podman reale (non fatto in questa sessione)

- Che `containers.create(networks={...})` produca davvero un container già attaccato alla rete indicata (assunto dal roadmap v2.0, non ri-verificato qui).
- Il campo esatto per un MAC statico su un'interfaccia collegata **dopo** la create (`connect_interface`): il codice logga un warning e non tenta di impostarlo, in attesa di questa verifica.
- Lo schema esatto di `container.attrs` nativo (`NetworkSettings.Networks`, `HostConfig.Sysctls`) restituito dall'endpoint libpod `/containers/{id}/json`, da cui dipendono `get_lab_from_api`/`update_lab_from_api`. Il codice usa già l'etichetta `kathara.ifaces` (JSON, scritta da Kathará stessa) invece di affidarsi a `NetworkSettings`/`DriverOpts` per la topologia delle interfacce, proprio per non dipendere da questo schema non verificato — ma i campi presi da `HostConfig`/`Config` (memoria, porte, sysctl) sì.
- La forma esatta del payload di `container.stats()` (P9).
- Comportamento multicast/broadcast su netavark (follow-up n.1, mai testato in nessuno spike, né v2.0 né v3.0).

## Cosa NON è stato rifatto qui

Gli esiti S6/S7/S8 della v2.0 che misurano il comportamento di **Podman stesso** (netavark, IPAM,
ordine interfacce, `get_registry_data` assente lato engine) restano quelli del documento originale:
non sono stati ripetuti perché richiedono un demone Podman reale, non disponibile in questo ambiente.
