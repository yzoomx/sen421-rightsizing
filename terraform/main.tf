# SEN421 right-sizing experiment on Hetzner Cloud.
#
#   load generator (k6) ──private network 10.0.1.0/24──► configuration under test
#                                                         (1 server, or N servers behind a load balancer)
#
# Targets have NO public inbound access: their firewall accepts only traffic
# from the private network range 10.0.0.0/16. Only the load generator
# accepts SSH (port 22).

locals {
  subnet       = "10.0.1.0/24"
  loadgen_ip   = cidrhost(local.subnet, 5)
  config_names = sort(keys(var.configs))
  labels       = { project = "sen421-rightsizing" }

  # One entry per target server, e.g. "d-2x-small-lb-2".
  instances = merge([
    for ci, name in local.config_names : {
      for n in range(var.configs[name].count) : "${name}-${n + 1}" => {
        config = name
        type   = var.configs[name].type
        ip     = cidrhost(local.subnet, 10 + ci * 10 + n)
      }
    }
  ]...)

  # Configurations with more than one server get a load balancer.
  lb_configs = {
    for ci, name in local.config_names : name => { ip = cidrhost(local.subnet, 100 + ci) }
    if var.configs[name].count > 1
  }

  # targets.txt for the load generator: name|server_type|count|entry_url|backend_urls
  targets_txt = join("\n", [
    for name in local.config_names : join("|", [
      name,
      var.configs[name].type,
      tostring(var.configs[name].count),
      var.configs[name].count > 1 ? "http://${try(local.lb_configs[name].ip, "")}:80" : "http://${local.instances["${name}-1"].ip}:8000",
      join(",", [for k in sort(keys(local.instances)) : "http://${local.instances[k].ip}:8000" if local.instances[k].config == name]),
    ])
  ])
}

resource "hcloud_ssh_key" "me" {
  name       = "sen421-rightsizing"
  public_key = file(pathexpand(var.ssh_public_key_path))
}

resource "hcloud_network" "net" {
  name     = "sen421-net"
  ip_range = "10.0.0.0/16"
  labels   = local.labels
}

resource "hcloud_network_subnet" "subnet" {
  network_id   = hcloud_network.net.id
  type         = "cloud"
  network_zone = var.network_zone
  ip_range     = local.subnet
}

# Inbound traffic is allowed ONLY from the private network (load generator and
# load balancer); everything from the internet is dropped. Outbound traffic
# (package and image downloads) stays allowed.
resource "hcloud_firewall" "targets" {
  name   = "sen421-targets-no-inbound"
  labels = local.labels
  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "8000"
    source_ips = ["10.0.0.0/16"]
  }
  rule {
    direction  = "in"
    protocol   = "icmp"
    source_ips = ["10.0.0.0/16"]
  }
}

resource "hcloud_firewall" "loadgen" {
  name   = "sen421-loadgen-ssh-only"
  labels = local.labels
  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "22"
    source_ips = var.ssh_allowed_cidrs
  }
  rule {
    direction  = "in"
    protocol   = "icmp"
    source_ips = var.ssh_allowed_cidrs
  }
}

resource "hcloud_server" "target" {
  for_each     = local.instances
  name         = "rs-${each.key}"
  server_type  = each.value.type
  image        = var.image
  location     = var.location
  ssh_keys     = [hcloud_ssh_key.me.id]
  firewall_ids = [hcloud_firewall.targets.id]
  labels       = merge(local.labels, { role = "target", config = each.value.config })

  public_net {
    ipv4_enabled = true # needed only to download Docker and the Python image
    ipv6_enabled = true
  }

  network {
    network_id = hcloud_network.net.id
    ip         = each.value.ip
    alias_ips  = []
  }

  user_data = templatefile("${path.module}/cloud-init-target.yaml.tftpl", {
    main_py      = base64encode(file("${path.module}/../app/main.py"))
    gen_data_py  = base64encode(file("${path.module}/../app/gen_data.py"))
    requirements = base64encode(file("${path.module}/../app/requirements.txt"))
    dockerfile   = base64encode(file("${path.module}/../app/Dockerfile"))
  })

  depends_on = [hcloud_network_subnet.subnet]
}

resource "hcloud_load_balancer" "lb" {
  for_each           = local.lb_configs
  name               = "rs-${each.key}"
  load_balancer_type = var.lb_type
  location           = var.location
  labels             = merge(local.labels, { role = "lb", config = each.key })
  algorithm {
    type = "round_robin"
  }
}

# The load balancer is attached to the private network only (no public interface).
resource "hcloud_load_balancer_network" "lb" {
  for_each                = local.lb_configs
  load_balancer_id        = hcloud_load_balancer.lb[each.key].id
  network_id              = hcloud_network.net.id
  ip                      = each.value.ip
  enable_public_interface = false
  depends_on              = [hcloud_network_subnet.subnet]
}

resource "hcloud_load_balancer_service" "http" {
  for_each         = local.lb_configs
  load_balancer_id = hcloud_load_balancer.lb[each.key].id
  protocol         = "http"
  listen_port      = 80
  destination_port = 8000

  health_check {
    protocol = "http"
    port     = 8000
    interval = 5
    timeout  = 3
    retries  = 3
    http {
      path         = "/health"
      status_codes = ["2??"]
    }
  }
}

resource "hcloud_load_balancer_target" "t" {
  for_each         = { for k, v in local.instances : k => v if contains(keys(local.lb_configs), v.config) }
  type             = "server"
  load_balancer_id = hcloud_load_balancer.lb[each.value.config].id
  server_id        = hcloud_server.target[each.key].id
  use_private_ip   = true
  depends_on       = [hcloud_load_balancer_network.lb]
}

resource "hcloud_server" "loadgen" {
  name         = "rs-loadgen"
  server_type  = var.loadgen_type
  image        = var.image
  location     = var.location
  ssh_keys     = [hcloud_ssh_key.me.id]
  firewall_ids = [hcloud_firewall.loadgen.id]
  labels       = merge(local.labels, { role = "loadgen" })

  public_net {
    ipv4_enabled = true
    ipv6_enabled = true
  }

  network {
    network_id = hcloud_network.net.id
    ip         = local.loadgen_ip
    alias_ips  = []
  }

  user_data = templatefile("${path.module}/cloud-init-loadgen.yaml.tftpl", {
    run_all       = base64encode(file("${path.module}/../scripts/run_all.sh"))
    targets       = base64encode(local.targets_txt)
    workload_js   = base64encode(file("${path.module}/../loadtest/workload.js"))
    saturation_js = base64encode(file("${path.module}/../loadtest/saturation.js"))
    step_js       = base64encode(file("${path.module}/../loadtest/step.js"))
  })

  depends_on = [hcloud_network_subnet.subnet]
}
