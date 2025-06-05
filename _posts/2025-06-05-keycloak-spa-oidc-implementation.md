---
title: "Client-Side OIDC Deep Dive: Implementing SPA Authentication with Keycloak"
excerpt: "A detailed walkthrough of implementing client-side OpenID Connect authentication using oidc-client-ts, including token management, logout handling, and production considerations."
categories:
  - Enterprise Authentication
  - SSO Implementation
tags:
  - keycloak
  - oidc
  - spa-authentication
  - javascript
  - oidc-client-ts
  - frontend-security
date: 2025-06-05
toc: true
---

In [Part 1]({{ site.baseurl }}{% post_url 2025-06-05-keycloak-sso-architecture-comparison %}), we explored the architectural differences between client-side and server-side authentication patterns. Now we'll dive deep into implementing robust client-side authentication for Single Page Applications (SPAs) using Keycloak and the `oidc-client-ts` library.

## SPA Authentication Fundamentals

Client-side authentication moves the OIDC flow into the browser, where JavaScript handles token acquisition, validation, and management. This approach provides excellent user experience but requires careful implementation to maintain security.

### Core Implementation Pattern

Here's the foundation of our SPA authentication:

```javascript
const oidcConfig = {
    authority: 'http://localhost:8080/realms/enterprise-sso',
    client_id: 'internal-portal',
    redirect_uri: window.location.origin + window.location.pathname,
    response_type: 'code',
    scope: 'openid profile email',
    automaticSilentRenew: true,
    loadUserInfo: true
};

let userManager = new oidc.UserManager(oidcConfig);
```

The configuration uses **Authorization Code flow with PKCE** (recommended for SPAs) rather than the deprecated implicit flow.

## Token Management Strategy

### Initial Authentication Check

Our initialization function handles multiple scenarios:

```javascript
async function initializeOIDC() {
    try {
        showStatus('Initializing authentication...', 'loading');
        userManager = new oidc.UserManager(oidcConfig);
        
        // Handle logout redirect
        if (window.location.pathname === '/logout') {
            showStatus('Logout received from SSO system...', 'loading');
            await userManager.removeUser();
            window.location.href = window.location.origin;
            return;
        }
        
        // Check existing authentication
        const user = await userManager.getUser();
        if (user && !user.expired) {
            const isValid = await validateTokenWithServer(user);
            if (isValid) {
                showUserSection(user);
            } else {
                await userManager.removeUser();
                showLoginSection();
            }
        } else {
            // Handle authorization callback
            if (window.location.search.includes('code=')) {
                showStatus('Processing login...', 'loading');
                try {
                    const user = await userManager.signinCallback();
                    showUserSection(user);
                    // Clean URL after successful callback
                    window.history.replaceState({}, document.title, window.location.pathname);
                } catch (error) {
                    showStatus('Login failed: ' + error.message, 'error');
                    showLoginSection();
                }
            } else {
                showLoginSection();
            }
        }
    } catch (error) {
        showStatus('Authentication system unavailable: ' + error.message, 'error');
        showLoginSection();
    }
}
```

### Server-Side Token Validation

Critical for security - we validate tokens against Keycloak's userinfo endpoint:

```javascript
async function validateTokenWithServer(user) {
    try {
        const response = await fetch(`${oidcConfig.authority}/protocol/openid-connect/userinfo`, {
            headers: { 'Authorization': 'Bearer ' + user.access_token }
        });
        return response.ok;
    } catch (error) {
        return false;
    }
}
```

This prevents using revoked or expired tokens that might still appear valid client-side.

## Login Flow Implementation

### Initiating Authentication

```javascript
async function login() {
    try {
        showStatus('Redirecting to login...', 'loading');
        await userManager.signinRedirect();
    } catch (error) {
        showStatus('Login failed: ' + error.message, 'error');
    }
}
```

The `signinRedirect()` method constructs the proper OIDC authorization URL and redirects the user to Keycloak's login page.

### Post-Login User Display

After successful authentication, we display user information:

```javascript
function showUserSection(user) {
    hideStatus();
    document.getElementById('login-section').style.display = 'none';
    document.getElementById('user-section').style.display = 'block';
    
    // Internal Portal user display
    document.getElementById('user-details').innerHTML = `
        <p><strong>Username:</strong> ${user.profile.preferred_username || 'N/A'}</p>
        <p><strong>Email:</strong> ${user.profile.email || 'N/A'}</p>
        <p><strong>Name:</strong> ${user.profile.name || user.profile.preferred_username || 'N/A'}</p>
        <p><strong>Roles:</strong> ${user.profile.realm_access?.roles?.join(', ') || 'employee'}</p>
        <p><strong>Login Time:</strong> ${new Date().toLocaleString()}</p>
    `;
}
```

Notice how we safely handle potentially missing profile fields using the `||` operator.

## Enterprise Logout Implementation

Logout in an enterprise SSO environment requires careful coordination across all authenticated applications.

### Logout Initiation

```javascript
async function logout() {
    try {
        showStatus('Logging out...', 'loading');
        const user = await userManager.getUser();
        
        if (user && user.id_token) {
            // Construct Keycloak logout URL with ID token hint
            const logoutUrl = `${oidcConfig.authority}/protocol/openid-connect/logout?` +
                `id_token_hint=${user.id_token}&` +
                `post_logout_redirect_uri=${encodeURIComponent(window.location.origin)}`;
            
            // Clear local tokens first
            await userManager.removeUser();
            
            // Redirect to Keycloak for global logout
            window.location.href = logoutUrl;
        } else {
            // Fallback: just clear local tokens
            await userManager.removeUser();
            showLoginSection();
        }
    } catch (error) {
        // Ensure logout even if there's an error
        await userManager.removeUser();
        showLoginSection();
    }
}
```

The `id_token_hint` parameter tells Keycloak which user session to terminate, enabling proper back-channel logout notifications to other applications.

## Back-Channel Logout Handling

Back-channel logout occurs when a user logs out from another application, and Keycloak notifies all other applications server-to-server.

### Logout Detection via Window Focus

Since SPAs can't directly receive back-channel notifications, we use window focus events to detect logout:

```javascript
window.addEventListener('focus', async () => {
    const user = await userManager.getUser();
    if (user && user.access_token) {
        const isValid = await validateTokenWithServer(user);
        if (!isValid) {
            await userManager.removeUser();
            showLoginSection();
        }
    }
});
```

This pattern ensures that when a user returns to a tab after logging out elsewhere, the application immediately detects the invalid session.

### Server-Side Logout Endpoint

Our Flask backend handles back-channel logout requests from Keycloak:

```javascript
// The /logout endpoint serves the same HTML page for back-channel requests
// JavaScript on page load can detect logout state and clean up accordingly
if (window.location.pathname === '/logout') {
    showStatus('Logout received from SSO system...', 'loading');
    await userManager.removeUser();
    window.location.href = window.location.origin;
    return;
}
```

## Error Handling and Edge Cases

### Network Connectivity Issues

```javascript
async function initializeOIDC() {
    try {
        // ... authentication logic
    } catch (error) {
        showStatus('Authentication system unavailable: ' + error.message, 'error');
        showLoginSection();
    }
}
```

### Token Expiration Handling

The `oidc-client-ts` library handles automatic token renewal with the `automaticSilentRenew: true` configuration:

```javascript
const oidcConfig = {
    // ... other config
    automaticSilentRenew: true,
    // Optional: customize renewal behavior
    silentRequestTimeout: 10000,
    accessTokenExpiringNotificationTime: 60
};
```

## UI State Management

### Status Display System

A reusable status system provides user feedback:

```javascript
function showStatus(message, type) {
    const statusDiv = document.getElementById('status');
    statusDiv.innerHTML = message;
    statusDiv.className = 'status ' + type;
    statusDiv.style.display = 'block';
}

function hideStatus() {
    document.getElementById('status').style.display = 'none';
}
```

With corresponding CSS:

```css
.status {
    padding: 10px;
    margin: 10px 0;
    border-radius: 4px;
}

.status.loading {
    background-color: #fff3cd;
    border: 1px solid #ffeaa7;
    color: #856404;
}

.status.error {
    background-color: #f8d7da;
    border: 1px solid #f5c6cb;
    color: #721c24;
}
```

## Production Considerations

### Security Best Practices

1. **HTTPS Only**: Never run OIDC flows over HTTP in production
2. **Content Security Policy**: Implement CSP headers to prevent XSS
3. **Token Storage**: Keep tokens in memory only, never in localStorage
4. **PKCE**: Always use Authorization Code + PKCE flow
5. **Token Validation**: Always validate tokens server-side

### Performance Optimizations

1. **Preload User Manager**: Initialize `oidc-client-ts` early in application lifecycle
2. **Silent Renewal**: Configure appropriate renewal timing
3. **Caching Strategy**: Cache user profile data appropriately
4. **Error Recovery**: Implement graceful degradation for auth failures

### Browser Compatibility

The `oidc-client-ts` library requires modern browser features:
- Promise support
- Fetch API
- Modern ES6 features

For older browser support, consider polyfills or the older `oidc-client` library.

## Keycloak Client Configuration

Your Keycloak client must be configured correctly:

```yaml
Client ID: internal-portal
Client Type: OpenID Connect
Client authentication: OFF (Public client)
Standard flow: ON
Direct access grants: OFF
Valid redirect URIs: http://localhost:3001/*
Valid post logout redirect URIs: http://localhost:3001
Web origins: http://localhost:3001
Backchannel logout URL: http://localhost:3001/logout
Backchannel logout session required: ON
```

## Portal-Specific Implementations

### Internal Portal Features

The internal portal focuses on employee services:

```html
<div class="feature-box">
    <h3>Quick Access</h3>
    <ul>
        <li>📅 Leave Management System</li>
        <li>💼 Employee Directory</li>
        <li>📊 Performance Dashboard</li>
        <li>🔧 IT Service Requests</li>
    </ul>
</div>
```

### External Portal Features

The external portal serves partners and vendors:

```html
<div class="feature-box">
    <h3>🏦 Banking Services</h3>
    <ul>
        <li>Account Verification System</li>
        <li>Transaction Processing Portal</li>
        <li>Credit Assessment Tools</li>
        <li>Regulatory Compliance Reports</li>
    </ul>
</div>
```

Both portals use identical authentication code but present different content and features.

## Testing and Debugging

### Browser Developer Tools

Monitor the authentication flow in browser dev tools:

1. **Network tab**: Watch for OIDC requests and responses
2. **Application tab**: Check for stored tokens (should be empty with proper implementation)
3. **Console**: Monitor JavaScript errors and authentication events

### Common Issues

**Problem**: Login redirect fails
**Solution**: Check redirect URI configuration in Keycloak

**Problem**: Silent renewal fails
**Solution**: Verify CORS settings and third-party cookie policies

**Problem**: Logout doesn't work across tabs
**Solution**: Implement window focus validation and back-channel logout handling

## Next Steps

In **[Part 3]({{ site.baseurl }}{% post_url 2025-06-05-keycloak-flask-server-side-authentication %})**, we'll explore the server-side Flask implementation, comparing how session management, authorization, and logout coordination work differently in a traditional web application architecture.

We'll also cover production deployment strategies, monitoring, and scaling considerations for both authentication patterns.

## Source Code

The complete SPA implementation is available in the [keycloak-training repository](https://github.com/lucidprogrammer/keycloak-training):

- `internal-portal/index.html` - Internal employee portal
- `external-portal/index.html` - External partner portal
- `README.md` - Complete setup instructions

---

*Building enterprise SPAs with secure authentication? I provide specialized training and consulting for Keycloak/IAM implementations. Connect with me on [Upwork](https://www.upwork.com/fl/lucidp) for expert guidance on your SSO project.*