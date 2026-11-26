terraform {
  required_version = ">= 1.5"
  required_providers {
    hcloud = {
      source  = "hetznercloud/hcloud"
      version = ">= 1.48, < 2.0"
    }
  }
}

# The API token is read from the HCLOUD_TOKEN environment variable.
# It is never written to any file in this repository.
provider "hcloud" {}
