/**
 * Validation dashboard.
 *
 * The findings themselves come from the backend untouched. What this module
 * adds is the answer to the question a finding always provokes: "so what do I
 * do about it?"
 *
 * The remedies below are keyed to the exact codes the validation engine
 * emits. Every one names a concrete edit — a prompt to rephrase, a Terraform
 * argument to change — rather than restating the problem in different words.
 * A code with no entry falls through to no remedy at all, which is honest;
 * inventing generic advice would be worse than staying quiet.
 */

import { clear, el, icon } from "./ui.js";

const SEVERITY_ORDER = { error: 0, warning: 1, info: 2 };

const GROUPS = {
  error: {
    label: "Errors",
    blurb: "These would fail during terraform apply.",
    icon: "alert",
    badge: "error",
  },
  warning: {
    label: "Warnings",
    blurb: "These deploy, but will cost you money, security or availability.",
    icon: "alert",
    badge: "warning",
  },
  info: {
    label: "Recommendations",
    blurb: "Worth knowing; nothing here blocks a deployment.",
    icon: "info",
    badge: "info",
  },
};

/**
 * code -> { fix, docs }
 *
 * `fix` is what to actually do. `docs` points at the AWS or Terraform page
 * that explains the underlying constraint, never at a search result.
 */
const REMEDIES = {
  // --- deployment-blocking -------------------------------------------
  load_balancer_single_az: {
    fix: "Ask for at least two availability zones — an Elastic Load Balancer "
       + "requires subnets in two. Add \"across 2 availability zones\" to your prompt.",
    docs: "https://docs.aws.amazon.com/elasticloadbalancing/latest/application/application-load-balancers.html",
  },
  eks_single_az: {
    fix: "Ask for two or more availability zones. EKS requires subnets in at "
       + "least two for the control plane to be created.",
    docs: "https://docs.aws.amazon.com/eks/latest/userguide/network-reqs.html",
  },
  multi_az_single_zone: {
    fix: "Either drop \"Multi-AZ\" from the prompt, or ask for two availability "
       + "zones so the standby has somewhere to live.",
    docs: "https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Concepts.MultiAZ.html",
  },
  aurora_as_instance: {
    fix: "Say \"Aurora cluster\" rather than \"Aurora database\". Aurora is an "
       + "aws_rds_cluster; the same engine on aws_db_instance is rejected at apply.",
    docs: "https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/rds_cluster",
  },
  missing_internet_gateway: {
    fix: "Add \"with an internet gateway\" to the prompt. A public subnet "
       + "without one has no route to the internet and is public in name only.",
    docs: "https://docs.aws.amazon.com/vpc/latest/userguide/VPC_Internet_Gateway.html",
  },
  circular_dependency: {
    fix: "Two resources each require the other. Name them separately in the "
       + "prompt so the dependency runs one way, or remove one of them.",
    docs: "https://developer.hashicorp.com/terraform/language/resources/behavior",
  },
  duplicate_cidr: {
    fix: "Two networks claim the same address range. State distinct CIDRs, "
       + "for example \"a VPC 10.0.0.0/16\" and \"a second VPC 10.1.0.0/16\".",
    docs: "https://docs.aws.amazon.com/vpc/latest/userguide/configure-your-vpc.html",
  },
  unsupported_provider: {
    fix: "This generator emits AWS Terraform only. Rewrite the requirement in "
       + "AWS terms, or use a provider-specific tool for that cloud.",
    docs: "https://registry.terraform.io/providers/hashicorp/aws/latest/docs",
  },
  resource_not_generated: {
    fix: "The design holds a resource the generator could not emit. Report the "
       + "prompt — the diagram and the code disagree, which is a defect.",
  },
  duplicate_id: {
    fix: "Two resources share an identifier, which Terraform will reject. "
       + "This is a generator defect rather than something the prompt caused — "
       + "please report the prompt that produced it.",
  },
  dangling_edge: {
    fix: "An edge points at a resource that is not in the design. Like a "
       + "duplicate id this is a generator defect, not a prompt problem.",
  },
  duplicate_resource_dropped: {
    fix: "More than one of this service was requested but only one was "
       + "generated. Generate one project per instance until this is supported.",
  },

  // --- security -------------------------------------------------------
  open_admin_port: {
    fix: "Restrict SSH or RDP to a known address. Say \"SSH from the office IP\" "
       + "instead of \"SSH access\", or use a bastion host.",
    docs: "https://docs.aws.amazon.com/vpc/latest/userguide/vpc-security-groups.html",
  },
  open_app_port: {
    fix: "Expected for a public web tier. If this is internal, say \"internal "
       + "load balancer\" or \"private\" so it is not opened to 0.0.0.0/0.",
    docs: "https://docs.aws.amazon.com/vpc/latest/userguide/vpc-security-groups.html",
  },
  public_database: {
    fix: "Add \"in private subnets\" to the prompt. A database reachable from "
       + "the internet is the single most common cause of data exposure.",
    docs: "https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_VPC.html",
  },
  public_bucket: {
    fix: "Say \"private bucket\" unless it is genuinely a public website "
       + "origin, in which case put CloudFront in front of it.",
    docs: "https://docs.aws.amazon.com/AmazonS3/latest/userguide/access-control-block-public-access.html",
  },
  unencrypted_storage: {
    fix: "Add \"encrypted\" or \"with a KMS key\" to the prompt.",
    docs: "https://docs.aws.amazon.com/AmazonS3/latest/userguide/serv-side-encryption.html",
  },
  missing_iam_role: {
    fix: "Add \"with an IAM role\" — compute cannot be granted permissions "
       + "without one, so it will not be able to reach other services.",
    docs: "https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles.html",
  },
  no_secret_store: {
    fix: "Add \"with Secrets Manager\" to keep the database password out of "
       + "state. The generated password is currently only in Terraform state.",
    docs: "https://docs.aws.amazon.com/secretsmanager/latest/userguide/intro.html",
  },
  unused_security_group: {
    fix: "Nothing is attached to this group. Name the resource it protects, "
       + "or drop it from the prompt.",
  },

  // --- reliability ------------------------------------------------------
  prod_single_az: {
    fix: "A production design in one availability zone fails with that zone. "
       + "Add \"highly available\" or \"across 2 availability zones\".",
    docs: "https://docs.aws.amazon.com/whitepapers/latest/aws-fault-isolation-boundaries/availability-zones.html",
  },
  db_single_az: {
    fix: "Add \"Multi-AZ\" so the database has a standby in a second zone.",
    docs: "https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Concepts.MultiAZ.html",
  },
  single_instance: {
    fix: "One instance means a single point of failure. Say \"two web servers\" "
       + "or \"an auto scaling group\" if this needs to stay up.",
  },
  private_subnet_without_nat: {
    fix: "Private compute has no outbound route. Add \"with a NAT gateway\" if "
       + "it needs to install packages or call external APIs.",
    docs: "https://docs.aws.amazon.com/vpc/latest/userguide/vpc-nat-gateway.html",
  },
  lb_no_targets: {
    fix: "The load balancer has nothing to forward to. Name the compute behind "
       + "it, for example \"a load balancer in front of two web servers\".",
  },
  monitoring_no_targets: {
    fix: "Monitoring was asked for but there is no compute or database to raise "
       + "alarms on. Add the resource you want watched.",
  },
  unattached_route_table: {
    fix: "This route table has no subnet to associate with. It is harmless but "
       + "will sit unused.",
  },

  // --- cost and naming ---------------------------------------------------
  nat_cost: {
    fix: "NAT gateways are billed hourly plus per GB and are usually the "
       + "largest line in a small design. One per AZ is only needed for zonal "
       + "resilience; say \"a single NAT gateway\" to halve it.",
    docs: "https://aws.amazon.com/vpc/pricing/",
  },
  oversized_nonprod: {
    fix: "This instance size is larger than a non-production environment "
       + "usually needs. State a smaller type, for example \"t3.small\".",
    docs: "https://aws.amazon.com/ec2/instance-types/",
  },
  name_prefix_shortened: {
    fix: "No action needed. Names are trimmed with a stable digest so load "
       + "balancer and target group names stay inside the 32-character cap.",
  },
  invalid_project_name: {
    fix: "Use only lowercase letters, digits and hyphens in the project name.",
  },
  orphan_resource: {
    fix: "This resource is not connected to anything else. Either it is "
       + "genuinely standalone, or the prompt did not say what uses it.",
  },
};

export function renderValidation(result, ctx = {}) {
  const { findings } = result;

  if (!findings.length) {
    return el("div", { class: "state" }, [
      el("div", {
        class: "state__icon",
        style: { background: "var(--success-bg)", color: "var(--success)" },
      }, [icon("check", 20)]),
      el("div", { class: "state__title", text: "No issues found" }),
      el("p", {
        class: "state__message",
        text: "This design passes every structural, network, security, "
            + "reliability and AWS deployment check.",
      }),
    ]);
  }

  const counts = { error: 0, warning: 0, info: 0 };
  for (const finding of findings) counts[finding.severity] += 1;

  const grouped = new Map();
  for (const finding of [...findings].sort(
    (a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity],
  )) {
    if (!grouped.has(finding.severity)) grouped.set(finding.severity, []);
    grouped.get(finding.severity).push(finding);
  }

  return el("div", { class: "stack" }, [
    /* --- summary strip --- */
    el("div", { class: "verdict-row" }, [
      verdictCard("error", counts.error, "would fail at apply"),
      verdictCard("warning", counts.warning, "deploys, but costs you something"),
      verdictCard("info", counts.info, "worth knowing"),
      el("div", {
        class: `verdict verdict--${counts.error ? "blocked" : "clear"}`,
      }, [
        el("span", { class: "verdict__label", text: "Deployment" }),
        el("span", { class: "verdict__value" }, [
          icon(counts.error ? "alert" : "check", 18),
          el("span", { text: counts.error ? "Blocked" : "Ready" }),
        ]),
        el("span", {
          class: "verdict__hint",
          text: counts.error
            ? "Fix the errors before running apply"
            : "Nothing blocks terraform apply",
        }),
      ]),
    ]),

    /* --- grouped findings --- */
    ...[...grouped].map(([severity, items]) => {
      const group = GROUPS[severity];
      return el("section", { class: "panel" }, [
        el("div", { class: "panel__header" }, [
          el("span", { class: `dot dot--${severity}` }),
          el("span", { class: "panel__title", text: group.label }),
          el("span", { class: `badge badge--${group.badge}`, text: String(items.length) }),
          el("span", { class: "panel__blurb", text: group.blurb }),
        ]),
        el("div", { class: "panel__body stack stack--tight" },
          items.map((finding) => findingCard(finding, result, ctx)),
        ),
      ]);
    }),
  ]);
}

function verdictCard(severity, count, hint) {
  return el("div", { class: `verdict verdict--${severity}${count ? "" : " is-zero"}` }, [
    el("span", { class: "verdict__label", text: GROUPS[severity].label }),
    el("span", { class: "verdict__value tabular", text: String(count) }),
    el("span", { class: "verdict__hint", text: hint }),
  ]);
}

/** One expandable finding, with a remedy when we have a real one. */
function findingCard(finding, result, ctx) {
  const remedy = REMEDIES[finding.code];
  const resource = finding.resource_id
    ? result.spec.resources.find((r) => r.id === finding.resource_id)
    : null;

  const body = el("div", { class: "finding-card__detail", hidden: true });
  let built = false;

  const toggle = el("button", {
    class: "finding-card__head",
    type: "button",
    "aria-expanded": "false",
    onClick: () => {
      const open = body.hidden;
      if (open && !built) {
        built = true;
        body.append(
          remedy
            ? el("div", { class: "remedy" }, [
                el("span", { class: "remedy__label" }, [
                  icon("bolt", 13), el("span", { text: "Suggested fix" }),
                ]),
                el("p", { class: "remedy__text", text: remedy.fix }),
                remedy.docs && el("a", {
                  class: "remedy__docs",
                  href: remedy.docs,
                  target: "_blank",
                  rel: "noreferrer noopener",
                }, [el("span", { text: "Read the documentation" }), icon("enter", 12)]),
              ])
            : el("p", { class: "remedy__none",
                text: "No specific remedy recorded for this check." }),
          resource && el("div", { class: "finding-card__resource" }, [
            el("span", { class: "finding-card__resource-label", text: "Affected resource" }),
            el("button", {
              class: "dep-chip dep-chip--action",
              type: "button",
              onClick: () => ctx.onSelectResource?.(resource),
            }, [
              el("span", { class: `resource-row__dot cat-${categoryOf(resource.kind)}` }),
              el("span", { text: `${resource.name} · ${resource.id}` }),
            ]),
            el("p", { class: "finding-card__reason", text: resource.reason }),
          ]),
        );
      }
      body.hidden = !open;
      toggle.setAttribute("aria-expanded", String(open));
    },
  }, [
    el("span", { class: `dot dot--${finding.severity}` }),
    el("span", { class: "finding-card__message", text: finding.message }),
    el("code", { class: "finding-card__code", text: finding.code }),
    el("span", { class: "finding-card__caret", "aria-hidden": "true" }),
  ]);

  return el("div", { class: `finding-card finding-card--${finding.severity}` }, [toggle, body]);
}

/** Mirrors the categorisation used by the diagram and the resource list. */
function categoryOf(kind) {
  if (/vm|instance|autoscaling|container|kubernetes|function|bastion|registry/.test(kind)) return "compute";
  if (/load_balancer|target_group|api_gateway|cdn|dns|certificate/.test(kind)) return "traffic";
  if (/sql|nosql|cache|warehouse/.test(kind)) return "data";
  if (/storage/.test(kind)) return "storage";
  if (/security|iam|secret|key|waf/.test(kind)) return "security";
  if (/queue|topic|event/.test(kind)) return "integration";
  if (/monitoring/.test(kind)) return "ops";
  return "network";
}
