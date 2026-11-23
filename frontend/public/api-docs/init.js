// The description of the API (TIME-26 AC-8): Swagger UI from this server, never from the internet, without
// "Try it out" (requests with an Origin header are refused anyway; the examples use curl).
window.addEventListener('load', function () {
  window.SwaggerUIBundle({
    url: '/api/v1/openapi.json',
    dom_id: '#swagger-ui',
    deepLinking: true,
    supportedSubmitMethods: [],
    tryItOutEnabled: false,
    validatorUrl: null,
    defaultModelsExpandDepth: 0,
  })
})
