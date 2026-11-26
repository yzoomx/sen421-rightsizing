# SEN421 – Right-Sizing on Hetzner Cloud: Cost per 1,000 Requests

Course project, SEN421 Cloud Architecture and Services (Fall 2026), Topic 4 (Part A).

**Question:** for a small study-app API, which Hetzner Cloud configuration serves
requests at the lowest cost per 1,000 requests while meeting a latency objective
(p95 < 100 ms), and when do two small servers behind a load balancer beat one bigger server?

## Alternatives compared

| Config | Servers | Why |
|---|---|---|
| a-small-shared | 1 × CPX22 (2 shared vCPU) | cheapest general option |
| b-large-shared | 1 × CPX32 (4 shared vCPU) | scale **up** |
| c-small-dedicated | 1 × CCX13 (2 dedicated vCPU) | shared vs dedicated CPU |
| d-2x-small-lb | 2 × CPX22 + load balancer LB11 | scale **out** |

Server types can change in `terraform/terraform.tfvars` if one is not orderable.

## Architecture

```
 your PC ──SSH──► load generator (k6) ──private network 10.0.1.0/24──► target(s)
                  public IPv4, port 22 only                           no public inbound ports
                                                     config d: ──► LB (private only) ──► 2 servers
```

* App: FastAPI + SQLite (synthetic data), Docker, one worker per vCPU (`app/`).
* cloud-init enables the private network interface with DHCP (netplan); on Hetzner Ubuntu 24.04 it otherwise stayed DOWN.
* Request mix: 70 % `GET /api/decks/{id}/cards` (read), 30 % `POST /api/reviews` (CPU-heavy grading).
* Test 1 – saturation: 64 virtual users, 90 s → maximum throughput.
* Test 2 – stepped load: 20 %…110 % of that throughput, 15 s per step → highest rate that meets the SLO.
* 3 repetitions per configuration, order rotated every repetition, 30 s cool-down between tests.
* CPU busy % and **steal %** read from `/proc/stat` before/after each test.
* Prices: official Hetzner API price list (`GET /v1/pricing`), saved with access date.

## Run it (Windows PowerShell) – about 2 hours, mostly waiting

### 0. One-time setup (15 min)
```powershell
winget install Hashicorp.Terraform        # or: choco install terraform
mkdir $HOME\.ssh -Force
ssh-keygen -t ed25519 -f $HOME\.ssh\sen421   # press Enter for no passphrase
```
In the **Hetzner Console**:
1. Create a new project, e.g. `sen421-rightsizing`.
2. **Usage → set a cost alert** (e.g. 5 in your billing currency) **before creating anything**. Screenshot it.
3. Project → Security → API tokens → **Generate API token** with *Read & Write*. Copy it.

### 1. Token and preflight (5 min)
```powershell
cd "E:\Cloud Architecture Project\sen421-rightsizing"
$env:HCLOUD_TOKEN = "paste-token-here"     # lives only in this window, never in a file
.\scripts\preflight.ps1
```
If PowerShell blocks the script: `Set-ExecutionPolicy -Scope Process Bypass` and run it again.
It prints which server types you can order in nbg1/fsn1/hel1 and saves
`results\pricing_<date>.json` (the official price source for the report).

```powershell
copy terraform\terraform.tfvars.example terraform\terraform.tfvars
notepad terraform\terraform.tfvars          # set location + types that preflight showed as orderable
```

### 2. Create the infrastructure (5–10 min)
```powershell
cd terraform
terraform init
terraform plan
terraform apply          # type: yes   -> screenshot the output (loadgen_ip)
```

### 3. Start the benchmark (about 70 min unattended)
```powershell
ssh -i $HOME\.ssh\sen421 root@<loadgen_ip>
```
On the server:
```bash
cloud-init status --wait            # wait until "status: done"
cat /opt/bench/targets.txt
nohup /opt/bench/run_all.sh > /opt/bench/run.log 2>&1 &
tail -f /opt/bench/run.log          # Ctrl+C stops watching, not the test
```
After the first configuration finishes (about 6 minutes) the log shows a line like
`saturation: 812.3 req/s, p95 140.2 ms, failed 0.00%`. If it shows `0.0 req/s` or a WARNING, stop
(`pkill -f run_all.sh`) and check `/opt/bench/results/*/run1_sat.json`.
Screenshot the log. You can close the window; the test keeps running.
It ends with `DONE - results in /opt/bench/results`.

### 4. Copy the results to your PC
```powershell
cd "E:\Cloud Architecture Project\sen421-rightsizing"
mkdir results\raw
scp -i $HOME\.ssh\sen421 -r root@<loadgen_ip>:/opt/bench/results/* results\raw\
```

### 5. DELETE EVERYTHING
```powershell
cd terraform
terraform destroy        # type: yes   -> screenshot "Destroy complete!"
```
Then check the Hetzner Console: no servers, load balancers, networks, firewalls or IPs left.
A few hours later, open **Usage** and screenshot the actual cost.

### 6. Analyse
```powershell
pip install matplotlib
python analysis\analyze.py --raw results\raw --location hel1
```
Creates `results\summary.csv`, `results\runs.csv`, `results\steps.csv`,
figures in `report\figures\` and LaTeX tables in `report\tables\`.

### 7. Report (Overleaf)
Zip the `report` folder, upload it to Overleaf (New Project → Upload Project) and compile `main.tex`.
Every red **WRITE** box is a writing note: replace it with your own text, then delete it.
Draw the architecture diagram in draw.io and export it as `report/figures/architecture.pdf`.

## Security and safety
* API token only in the `HCLOUD_TOKEN` environment variable; `terraform.tfvars` and state are git-ignored.
* Targets: Hetzner firewall that accepts traffic **only from the private range 10.0.0.0/16** → unreachable from the internet.
* Load balancer: private network only (`enable_public_interface = false`).
* Load generator: SSH key login only, port 22 only (restrict with `ssh_allowed_cidrs`).
* Synthetic data only; load tests target only resources in this account.

## Repository layout
```
app/        FastAPI benchmark app + Dockerfile + synthetic data generator
loadtest/   k6 scripts (workload.js, saturation.js, step.js)
terraform/  Hetzner infrastructure (servers, private network, firewalls, LB)
scripts/    preflight.ps1 (availability + prices), run_all.sh (benchmark runner)
analysis/   analyze.py (tables, figures, cost per 1,000 requests)
report/     IEEE LaTeX report (main.tex), figures/ and tables/ filled by analyze.py
results/    raw results and price snapshot
```
