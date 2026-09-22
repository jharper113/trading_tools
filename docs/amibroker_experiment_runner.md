# AmiBroker experiment runner

The experiment runner executes the five-job optimization pilot sequentially,
checks the live intraday database timezone, and keeps every job in a separate
hashed run archive. Wait for Dropbox to finish syncing and close any other
AmiBroker Analysis job before starting it.

From Windows PowerShell:

```powershell
powershell.exe -ExecutionPolicy Bypass -File `
  "Z:\04_Code\Python\trading_tools\Run-AmiBroker-Experiment.ps1" `
  -Mode pilot `
  -Matrix "Z:\04_Code\Python\trading_tools\amibroker_experiments\strategy_test_matrix.json" `
  -Broker "C:\Program Files (x86)\AmiBroker\Broker.exe" `
  -ReportsRoot "Z:\04_Code\Amibroker\Reports"
```

The runner prints the experiment manifest path. Experiments use this layout:

```text
Z:\04_Code\Amibroker\Reports\Experiments\<experiment-id>\
  experiment_manifest.json
  strategy_test_matrix.json
  Timezone_Preflight\
  jobs\
```

The timezone preflight blocks all pilot jobs unless `Harp_intraday` has a
5-minute base interval, reports a zero-second AmiBroker time shift, and contains
ES 09:30 and 15:55 Eastern bars in both winter and summer samples. The importer
has already converted timestamps with `America/Detroit` daylight-saving rules;
AmiBroker must not shift them again.

If Windows or AmiBroker stops during the experiment, rerun with the exact
manifest printed by the first command:

```powershell
powershell.exe -ExecutionPolicy Bypass -File `
  "Z:\04_Code\Python\trading_tools\Run-AmiBroker-Experiment.ps1" `
  -Resume "Z:\04_Code\Amibroker\Reports\Experiments\20260921T120000000Z_ab12cd34\experiment_manifest.json" `
  -Broker "C:\Program Files (x86)\AmiBroker\Broker.exe" `
  -ReportsRoot "Z:\04_Code\Amibroker\Reports"
```

Resume revalidates every completed archive and skips it only when all recorded
hashes still match. A job left `RUNNING` becomes `INTERRUPTED`; its partial
archive remains available, and the new attempt uses a new directory.

Experiment and job states mean:

- `PENDING`: AmiBroker has not started the job.
- `RUNNING`: the current attempt has started.
- `COMPLETE`: all expected optimization and entry-audit CSVs were archived and
  their hashes match. This does **not** mean the strategy passed its gates.
- `FAILED`: generation, AmiBroker execution, export validation, or hashing
  failed.
- `INTERRUPTED`: a prior controller stopped before its attempt completed.

After the pilot, run the Python analyzer and audit documented in
`docs/amibroker_experiment_analysis.md`. A passing audit creates both an unlock
artifact and a sealed expanded matrix. Full-library mode rejects the compact
catalog matrix and requires those two audit outputs:

```powershell
powershell.exe -ExecutionPolicy Bypass -File `
  "Z:\04_Code\Python\trading_tools\Run-AmiBroker-Experiment.ps1" `
  -Mode full `
  -Matrix "Z:\04_Code\Amibroker\Reports\Experiments\PILOT_ID\Analysis\full_strategy_test_matrix.json" `
  -Unlock "Z:\04_Code\Amibroker\Reports\Experiments\PILOT_ID\Analysis\full_run_unlock.json" `
  -Broker "C:\Program Files (x86)\AmiBroker\Broker.exe" `
  -ReportsRoot "Z:\04_Code\Amibroker\Reports"
```

The runner never deletes old experiments, interrupted staging files, or archived
runs. Review them manually before removing anything.
