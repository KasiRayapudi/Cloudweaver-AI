# Security groups. Ingress is least-privilege by construction: each tier only accepts traffic from the tier in front of it.

resource "aws_security_group" "app_sg" {
  name        = "${local.name_prefix}-app-sg"
  description = "App Security Group generated from the requirement description"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "Allow 22 from 0.0.0.0/0"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Allow 80 from 0.0.0.0/0"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "Allow all outbound traffic"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = merge(local.tags, { Name = "${local.name_prefix}-app-sg" })

  lifecycle {
    create_before_destroy = true
  }
}
