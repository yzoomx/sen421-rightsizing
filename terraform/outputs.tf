output "loadgen_ip" {
  description = "Public IPv4 address of the load generator."
  value       = hcloud_server.loadgen.ipv4_address
}

output "ssh_command" {
  value = "ssh -i ~/.ssh/sen421 root@${hcloud_server.loadgen.ipv4_address}"
}

output "targets" {
  description = "Contents of /opt/bench/targets.txt on the load generator."
  value       = local.targets_txt
}

output "server_types" {
  value = { for k, s in hcloud_server.target : k => s.server_type }
}
