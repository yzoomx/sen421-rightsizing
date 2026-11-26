variable "location" {
  description = "Hetzner location for every resource (run scripts/preflight.ps1 to see what is orderable where)."
  type        = string
  default     = "nbg1"
}

variable "network_zone" {
  description = "Network zone of the location: eu-central for nbg1, fsn1 and hel1."
  type        = string
  default     = "eu-central"
}

variable "image" {
  description = "Operating system image for all servers."
  type        = string
  default     = "ubuntu-24.04"
}

variable "ssh_public_key_path" {
  description = "Path to your SSH public key (created with ssh-keygen)."
  type        = string
  default     = "~/.ssh/sen421.pub"
}

variable "ssh_allowed_cidrs" {
  description = "Addresses allowed to SSH into the load generator. Set to [\"<your-ip>/32\"] to restrict it."
  type        = list(string)
  default     = ["0.0.0.0/0", "::/0"]
}

variable "loadgen_type" {
  description = "Server type of the load generator. It must be larger than the targets so it is never the bottleneck."
  type        = string
  default     = "cpx32"
}

variable "lb_type" {
  description = "Load balancer type used by configurations with more than one server."
  type        = string
  default     = "lb11"
}

variable "configs" {
  description = <<-EOT
    The alternatives being compared. Each entry is one configuration under test.
    count = 1 -> a single server; count > 1 -> that many servers behind a load balancer.
    Names are sorted alphabetically to assign private IP addresses.
  EOT
  type = map(object({
    type  = string
    count = number
  }))
  default = {
    "a-small-shared"    = { type = "cpx22", count = 1 }
    "b-large-shared"    = { type = "cpx32", count = 1 }
    "c-small-dedicated" = { type = "ccx13", count = 1 }
    "d-2x-small-lb"     = { type = "cpx22", count = 2 }
  }
}
