<#
  Preflight check (run on Windows PowerShell before terraform apply).

  1. Lists which server types can be ordered right now in each Hetzner location,
     (EU locations nbg1, fsn1, hel1) with vCPUs, memory and the official net
     price (in your account's billing currency, excl. VAT).
  2. Saves a dated snapshot of Hetzner's official price list from the API
     (GET /v1/pricing) to ..\results\pricing_<date>.json. This file is the price
     source for the cost analysis (cite it with its access date).
  3. Prints your public IP, so you can restrict SSH in terraform.tfvars.

  Usage:
    $env:HCLOUD_TOKEN = "<your read/write API token>"
    .\scripts\preflight.ps1
#>
$ErrorActionPreference = 'Stop'
if (-not $env:HCLOUD_TOKEN) {
    Write-Host 'Set your token first:  $env:HCLOUD_TOKEN = "..."' -ForegroundColor Red
    exit 1
}
$api = 'https://api.hetzner.cloud/v1'
$headers = @{ Authorization = "Bearer $($env:HCLOUD_TOKEN)" }

$serverTypes = (Invoke-RestMethod -Headers $headers -Uri "$api/server_types?per_page=50").server_types
$pricing     = (Invoke-RestMethod -Headers $headers -Uri "$api/pricing").pricing

# Availability per location comes from server_types[].locations[].available
# (the old /v1/datacenters endpoint was removed by Hetzner on 1 Oct 2026).
Write-Host "`nServer types you can order NOW (prices: $($pricing.currency) net, excl. VAT)`n" -ForegroundColor Cyan
$rows = @()
foreach ($t in $serverTypes) {
    foreach ($loc in $t.locations) {
        if ($loc.available -ne $true) { continue }
        if ($loc.deprecation) { continue }
        $p = $t.prices | Where-Object { $_.location -eq $loc.name } | Select-Object -First 1
        $rows += [pscustomobject]@{
            Location  = $loc.name
            Type      = $t.name
            Category  = $t.category
            vCPU      = $t.cores
            CPU       = $t.cpu_type
            Arch      = $t.architecture
            RAM_GB    = $t.memory
            Disk_GB   = $t.disk
            Price_hour  = if ($p) { [math]::Round([double]$p.price_hourly.net, 4) } else { $null }
            Price_month = if ($p) { [math]::Round([double]$p.price_monthly.net, 2) } else { $null }
        }
    }
}
if ($rows.Count -eq 0) {
    Write-Host 'The API did not mark any type as available. Supported types per location (availability unknown):' -ForegroundColor Yellow
    foreach ($t in $serverTypes) {
        Write-Host ("  {0,-8} {1} vCPU {2} GB  -> {3}" -f $t.name, $t.cores, $t.memory, (($t.locations | ForEach-Object { $_.name }) -join ', '))
    }
    Write-Host 'Check the Create Server page in the Hetzner Console to see what is orderable.' -ForegroundColor Yellow
} else {
    $rows | Where-Object { $_.Location -in @('nbg1', 'fsn1', 'hel1') } | Sort-Object Location, vCPU, Type | Format-Table -AutoSize
}

Write-Host "Load balancer types ($($pricing.currency) net):" -ForegroundColor Cyan
foreach ($lb in $pricing.load_balancer_types) {
    foreach ($p in $lb.prices) {
        Write-Host ("  {0,-6} {1,-5} {2,8} /hour {3,8} /month" -f $lb.name, $p.location, [math]::Round([double]$p.price_hourly.net, 4), [math]::Round([double]$p.price_monthly.net, 2))
    }
}

# Save the official price snapshot with its access date (UTC).
$stamp = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd')
$outDir = Join-Path $PSScriptRoot '..\results'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$snapshot = [ordered]@{
    accessed_utc = (Get-Date).ToUniversalTime().ToString('o')
    source       = "$api/pricing and $api/server_types (Hetzner Cloud API)"
    pricing      = $pricing
    server_types = $serverTypes
}
$outFile = Join-Path $outDir "pricing_$stamp.json"
$snapshot | ConvertTo-Json -Depth 20 | Set-Content -Encoding UTF8 -Path $outFile
Write-Host "`nSaved official price snapshot to $outFile" -ForegroundColor Green

try {
    $ip = Invoke-RestMethod -Uri 'https://api.ipify.org'
    Write-Host "Your public IP is $ip  ->  in terraform.tfvars you can set: ssh_allowed_cidrs = [`"$ip/32`"]"
} catch {
    Write-Host 'Could not detect your public IP (optional).'
}
