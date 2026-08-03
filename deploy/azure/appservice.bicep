@description('Career Copilot — Azure App Service (student-friendly)')
param location string = 'eastus'
param serviceName string = 'career-copilot'

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

resource appPlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${serviceName}-plan'
  location: location
  sku: { name: 'F1'; tier: 'Free' }
}

resource botApp 'Microsoft.Web/sites@2023-12-01' = {
  name: '${serviceName}-bot'
  location: location
  kind: 'app,linux'
  properties: {
    serverFarmId: appPlan.id
    siteConfig: {
      linuxFxVersion: 'DOCKER|career-copilot:latest'
      appSettings: [
        { name: 'TELEGRAM_BOT_TOKEN'; value: telegramBotToken }
        { name: 'GEMINI_API_KEY'; value: geminiApiKey }
        { name: 'DEEPSEEK_API_KEY'; value: deepseekApiKey }
        { name: 'VOYAGE_API_KEY'; value: voyageApiKey }
        { name: 'DATABASE_URL'; value: databaseUrl }
        { name: 'TAVILY_API_KEY'; value: tavilyApiKey }
        { name: 'FIRECRAWL_API_KEY'; value: firecrawlApiKey }
        { name: 'GITHUB_TOKEN'; value: githubToken }
        { name: 'WEBSITES_PORT'; value: '8080' }
      ]
    }
  }
}

output botUrl string = botApp.properties.defaultHostName
