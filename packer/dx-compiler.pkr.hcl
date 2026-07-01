packer {
  required_plugins {
    amazon = {
      version = ">= 1.2.0"
      source  = "github.com/hashicorp/amazon"
    }
  }
}

variable "dx_com_version" {
  type        = string
  default     = "latest"
  description = "Version of dx-com package to install. Use 'latest' or a specific version like '2.3.0'."
}

variable "instance_type" {
  type        = string
  default     = "t3.xlarge"
  description = "EC2 instance type used during AMI build."
}

source "amazon-ebs" "dx-compiler" {
  ami_name      = "dx-compiler-{{timestamp}}"
  instance_type = var.instance_type
  region        = "us-east-1"

  source_ami_filter {
    filters = {
      name                = "ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"
      root-device-type    = "ebs"
      virtualization-type = "hvm"
    }
    most_recent = true
    owners      = ["099720109477"] # Canonical
  }

  launch_block_device_mappings {
    device_name           = "/dev/sda1"
    volume_size           = 30
    volume_type           = "gp3"
    delete_on_termination = true
  }

  ssh_username = "ubuntu"

  tags = {
    Name       = "DX Compiler AMI"
    Base_AMI   = "{{ .SourceAMI }}"
    Build_Time = "{{timestamp}}"
  }
}

build {
  sources = ["source.amazon-ebs.dx-compiler"]

  # --- Build Phase ---

  provisioner "shell" {
    inline_shebang = "/bin/bash -e"
    inline = [
      "echo '[INFO] Updating system packages'",
      "sudo rm -rf /var/lib/apt/lists/*",
      "sudo apt-get clean",
      "echo '[INFO] Ensuring apt sources are configured'",
      "sudo add-apt-repository -y main || true",
      "sudo add-apt-repository -y universe || true",
      "echo '[INFO] Running apt-get update'",
      "sudo apt-get update",
      "echo '[INFO] apt-get update completed successfully'",
      "sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq",
    ]
  }

  provisioner "shell" {
    inline = [
      "echo '[INFO] Installing system dependencies'",
      "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends curl unzip build-essential libgl1-mesa-glx libglib2.0-0 python3 python3-dev python3-venv",
      "python3 --version",
    ]
  }

  provisioner "shell" {
    inline_shebang = "/bin/bash -e"
    inline = [
      "echo '[INFO] Ensuring SSM Agent is installed and running'",
      "if ! snap list amazon-ssm-agent &>/dev/null; then sudo snap install amazon-ssm-agent --classic; fi",
      "sudo snap start amazon-ssm-agent",
      "sudo snap services amazon-ssm-agent",
      "echo '[INFO] SSM Agent is active'",
    ]
  }

  provisioner "shell" {
    inline_shebang = "/bin/bash -e"
    inline = [
      "echo '[INFO] Installing AWS CLI v2'",
      "if command -v aws &>/dev/null; then echo 'AWS CLI already installed'; aws --version; exit 0; fi",
      "curl -fsSL 'https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip' -o /tmp/awscliv2.zip",
      "unzip -q /tmp/awscliv2.zip -d /tmp",
      "sudo /tmp/aws/install",
      "rm -rf /tmp/awscliv2.zip /tmp/aws",
      "aws --version",
    ]
  }

  provisioner "shell" {
    inline_shebang = "/bin/bash -e"
    inline = [
      "set -euo pipefail",
      "echo '[INFO] Creating Python virtual environment for dx-com'",
      "sudo mkdir -p /opt/dx-compiler",
      "sudo chown $(id -u):$(id -g) /opt/dx-compiler",
      "python3 -m venv /opt/dx-compiler/venv",
      "source /opt/dx-compiler/venv/bin/activate",
      "pip install --upgrade pip",
      "if [ '${var.dx_com_version}' = 'latest' ]; then pip install dx-com; else pip install 'dx-com==${var.dx_com_version}'; fi",
      "dxcom -v",
      "deactivate",
      "echo '[INFO] dx-com installed at /opt/dx-compiler/venv'",
    ]
  }

  provisioner "shell" {
    inline = [
      "echo '[INFO] Pre-downloading calibration dataset'",
      "sudo mkdir -p /opt/dx-compiler/calibration_dataset",
      "curl -fsSL 'https://sdk.deepx.ai/dataset/calibration_dataset.tar.gz' -o /tmp/calibration_dataset.tar.gz",
      "sudo tar -xzf /tmp/calibration_dataset.tar.gz -C /opt/dx-compiler/calibration_dataset",
      "rm -f /tmp/calibration_dataset.tar.gz",
      "echo '[INFO] Calibration dataset cached at /opt/dx-compiler/calibration_dataset'",
    ]
  }

  provisioner "shell" {
    inline_shebang = "/bin/bash -e"
    inline = [
      "echo '[INFO] Creating dxcom wrapper script'",
      "sudo tee /usr/local/bin/dx-compile > /dev/null <<'EOF'\n#!/bin/bash\nset -Eeuo pipefail\nsource /opt/dx-compiler/venv/bin/activate\nexec dxcom \"$@\"\nEOF",
      "sudo chmod +x /usr/local/bin/dx-compile",
      "echo '[INFO] Wrapper script created at /usr/local/bin/dx-compile'",
    ]
  }

  provisioner "shell" {
    inline = [
      "echo '[INFO] Cleaning up'",
      "sudo apt-get clean",
      "sudo rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*",
      "echo '[INFO] Build phase complete'",
    ]
  }

  # --- AWS Marketplace Security Hardening ---

  provisioner "shell" {
    inline = [
      "echo '[INFO] Marketplace security hardening - removing SSH host keys'",
      "sudo rm -f /etc/ssh/ssh_host_*",
      "echo '[INFO] Removing authorized_keys'",
      "sudo rm -f /home/ubuntu/.ssh/authorized_keys",
      "sudo rm -f /root/.ssh/authorized_keys",
      "echo '[INFO] Clearing bash history'",
      "cat /dev/null > ~/.bash_history",
      "sudo su - root -c 'cat /dev/null > ~/.bash_history'",
      "echo '[INFO] Clearing log files'",
      "sudo find /var/log -type f -exec cp /dev/null {} \\;",
      "echo '[INFO] Marketplace security hardening complete'",
    ]
  }

  # --- Validate Phase ---

  provisioner "shell" {
    inline_shebang = "/bin/bash -e"
    inline = [
      "echo '[INFO] Running validations'",
      "python3 --version",
      "/opt/dx-compiler/venv/bin/python --version",
      "source /opt/dx-compiler/venv/bin/activate && dxcom -v && dx-compile -v",
      "aws --version",
      "test -d /opt/dx-compiler/calibration_dataset",
      "echo '[INFO] Calibration dataset directory exists'",
      "ls /opt/dx-compiler/calibration_dataset | head -5",
      "df -h /",
      "echo '[INFO] All validations passed'",
    ]
  }

  # --- Test Phase ---

  provisioner "shell" {
    inline_shebang = "/bin/bash -e"
    inline = [
      "set -euo pipefail",
      "source /opt/dx-compiler/venv/bin/activate",
      "python -c \"import dx_com; print(dx_com.__version__)\"",
      "echo '[INFO] dx-com import test passed'",
    ]
  }
}
