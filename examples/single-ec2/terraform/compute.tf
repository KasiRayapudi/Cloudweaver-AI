# Compute resources.

resource "aws_instance" "app_server" {
  ami                    = data.aws_ami.os.id
  instance_type          = "t3.micro"
  subnet_id              = aws_subnet.public[0].id
  vpc_security_group_ids = [aws_security_group.app_sg.id]
  iam_instance_profile   = aws_iam_instance_profile.instance.name
  key_name               = var.key_pair_name
  user_data              = file("${path.module}/user_data.sh")

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
    encrypted   = true
  }
  tags = merge(local.tags, { Name = "${local.name_prefix}-app" })
}
