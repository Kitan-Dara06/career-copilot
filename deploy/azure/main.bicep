@description('Career Copilot — Azure Container Apps')
param location string = resourceGroup().location

@secure()
param telegramBotToken string
@secure()
param geminiApiKey string
@secure()
param deepseekApiKey string
@secure()
param voyageApiKey string
@secure()
param databaseUrl string
@secure()
param tavilyApiKey string = ''
@secure()
param firecrawlApiKey string = ''
@secure()
param githubToken string = ''

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'careercopilot'
  location: location
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: true }
}

resource containerEnv 'Microsoft.App/managedEnvironments@2023-11-02-preview' = {
  name: 'career-copilot-env'
  location: location
  properties: {}
}

resource botApp 'Microsoft.App/containerApps@2023-11-02-preview' = {
  name: 'career-copilot-bot'
  location: location
  properties: {
    managedEnvironmentId: containerEnv.id
    configuration: {
      ingress: null
      secrets: [
        {
          name: 'acr-password'
          value: acr.listCredentials().passwords[0].value
        }
      ]
      registries: [{
        server: acr.properties.loginServer
        username: acr.listCredentials().username
        passwordSecretRef: 'acr-password'
      }]
    }
    template: {
      containers: [{
        name: 'bot'
        image: '${acr.properties.loginServer}/career-copilot:latest'
        command: [ '/app/docker-entrypoint.sh', 'serve', '--polling' ]
        env: [
          {
            name: 'TELEGRAM_BOT_TOKEN'
            value: telegramBotToken
          }
          {
            name: 'GEMINI_API_KEY'
            value: geminiApiKey
          }
          {
            name: 'DEEPSEEK_API_KEY'
            value: deepseekApiKey
          }
          {
            name: 'VOYAGE_API_KEY'
            value: voyageApiKey
          }
          {
            name: 'DATABASE_URL'
            value: databaseUrl
          }
          {
            name: 'TAVILY_API_KEY'
            value: tavilyApiKey
          }
          {
            name: 'FIRECRAWL_API_KEY'
            value: firecrawlApiKey
          }
          {
            name: 'GITHUB_TOKEN'
            value: githubToken
          }
        ]
        resources: {
          cpu: json('0.5')
          memory: '1.0Gi'
        }
      }]
    }
  }
}

output acrLoginServer string = acr.properties.loginServer
output botAppName string = botApp.name
