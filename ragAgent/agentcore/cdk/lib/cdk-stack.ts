import {
  AgentCoreApplication,
  AgentCoreMcp,
  type AgentCoreProjectSpec,
  type AgentCoreMcpSpec,
} from '@aws/agentcore-cdk';
import { CfnOutput, Stack, type StackProps } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as ecs_patterns from 'aws-cdk-lib/aws-ecs-patterns';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as iam from 'aws-cdk-lib/aws-iam';

export interface AgentCoreStackProps extends StackProps {
  /**
   * The AgentCore project specification containing agents, memories, and credentials.
   */
  spec: AgentCoreProjectSpec;
  /**
   * The MCP specification containing gateways and servers.
   */
  mcpSpec?: AgentCoreMcpSpec;
  /**
   * Credential provider ARNs from deployed state, keyed by credential name.
   */
  credentials?: Record<string, { credentialProviderArn: string; clientSecretArn?: string }>;
}

/**
 * CDK Stack that deploys AgentCore infrastructure.
 *
 * This is a thin wrapper that instantiates L3 constructs.
 * All resource logic and outputs are contained within the L3 constructs.
 */
export class AgentCoreStack extends Stack {
  /** The AgentCore application containing all agent environments */
  public readonly application: AgentCoreApplication;

  constructor(scope: Construct, id: string, props: AgentCoreStackProps) {
    super(scope, id, props);

    const { spec, mcpSpec, credentials } = props;

    // Create AgentCoreApplication with all agents
    this.application = new AgentCoreApplication(this, 'Application', {
      spec,
    });

    // Create AgentCoreMcp if there are gateways configured
    if (mcpSpec?.agentCoreGateways && mcpSpec.agentCoreGateways.length > 0) {
      new AgentCoreMcp(this, 'Mcp', {
        projectName: spec.name,
        mcpSpec,
        agentCoreApplication: this.application,
        credentials,
        projectTags: spec.tags,
      });
    }

    // Create VPC (cost-effective: 0 NAT Gateways, public subnets only)
    const vpc = new ec2.Vpc(this, 'UiVpc', {
      maxAzs: 2,
      natGateways: 0,
      subnetConfiguration: [
        {
          cidrMask: 24,
          name: 'Public',
          subnetType: ec2.SubnetType.PUBLIC,
        }
      ]
    });

    // Create ECS Cluster
    const cluster = new ecs.Cluster(this, 'UiCluster', { vpc });

    // Reference ECR Repository
    const uiRepo = ecr.Repository.fromRepositoryName(this, 'UiRepo', 'rag-agent-ui');

    // Create Fargate Service with Application Load Balancer
    const fargateService = new ecs_patterns.ApplicationLoadBalancedFargateService(this, 'StreamlitService', {
      cluster,
      cpu: 256,
      memoryLimitMiB: 512,
      desiredCount: 1,
      publicLoadBalancer: true,
      assignPublicIp: true,
      taskImageOptions: {
        image: ecs.ContainerImage.fromEcrRepository(uiRepo, 'latest'),
        containerPort: 8501,
        environment: {
          RUNTIME_ARN: 'arn:aws:bedrock-agentcore:us-east-1:911268715109:runtime/ragAgent_MyAgent-3AkJyICSTJ',
          REGION: 'us-east-1',
        }
      }
    });

    // Configure health check to use Streamlit's health endpoint
    fargateService.targetGroup.configureHealthCheck({
      path: '/_stcore/health',
      port: '8501',
    });

    // Grant permissions to invoke Bedrock AgentCore Runtime
    fargateService.taskDefinition.addToTaskRolePolicy(new iam.PolicyStatement({
      actions: [
        'bedrock-agentcore:InvokeAgentRuntime',
        'bedrock-agentcore:InvokeAgent',
        'bedrock:InvokeModel',
        'bedrock:InvokeModelWithResponseStream'
      ],
      resources: ['*']
    }));

    // Output the Load Balancer URL
    new CfnOutput(this, 'UiUrl', {
      description: 'URL of the Streamlit UI',
      value: `http://${fargateService.loadBalancer.loadBalancerDnsName}`,
    });

    // Stack-level output
    new CfnOutput(this, 'StackNameOutput', {
      description: 'Name of the CloudFormation Stack',
      value: this.stackName,
    });
  }
}
