---
title: "Building Multi-Architecture SSO: SPA vs Server-Side Authentication with Keycloak"
excerpt: "A practical comparison of client-side and server-side authentication patterns in a single Keycloak implementation, exploring when to use each approach."
categories:
  - Enterprise Authentication
  - SSO Implementation
tags:
  - keycloak
  - oidc
  - sso
  - spa-authentication
  - flask
  - enterprise-security
date: 2025-06-05
toc: true
---

When implementing enterprise Single Sign-On (SSO), one of the first architectural decisions you'll face is choosing between client-side and server-side authentication patterns. Each approach has distinct security, performance, and user experience implications.

In this post, I'll walk through a practical implementation that demonstrates both patterns within a single Keycloak deployment, helping you understand when to use each approach.

## The Authentication Architecture Challenge

Modern enterprise applications often need different authentication patterns for different user types and security requirements:

- **Employee portals** might prioritize user experience and rapid development
- **Administrative interfaces** typically require enhanced security and audit trails
- **Partner systems** may need simplified integration patterns

Rather than forcing a one-size-fits-all approach, we can implement multiple authentication patterns within the same SSO ecosystem.

## Demo Architecture Overview

Our implementation includes three portals, each demonstrating different authentication approaches:

### Portal Types

**Internal Portal (SPA)**
- Client-side authentication using JavaScript
- Token management in browser
- Modern single-page application experience

**External Portal (SPA)**  
- Similar client-side pattern
- Demonstrates cross-portal SSO behavior
- Partner/vendor interface simulation

**Admin Dashboard (Server-Side)**
- Flask-based server-side authentication
- Session management on server
- Enhanced security for administrative functions

## Authentication Pattern Comparison

### Client-Side (SPA) Pattern

**How it works:**
```javascript
// Browser handles OIDC flow directly
const userManager = new oidc.UserManager({
    authority: 'http://localhost:8080/realms/enterprise-sso',
    client_id: 'internal-portal',
    redirect_uri: window.location.origin,
    response_type: 'code',
    scope: 'openid profile email'
});

// Tokens stored in browser memory
const user = await userManager.getUser();
```

**Advantages:**
- Fast, responsive user experience
- Reduced server-side complexity
- Modern development patterns
- Easy to scale horizontally

**Trade-offs:**
- Tokens accessible via JavaScript
- Browser-based session management
- Requires careful XSS protection

### Server-Side Pattern

**How it works:**
```python
# Server handles OIDC flow
@app.route('/admin/callback')
def admin_callback():
    token = keycloak_client.authorize_access_token()
    user_info = token.get('userinfo')
    
    # Store in server session
    session['user'] = {
        'id': user_info.get('sub'),
        'username': user_info.get('preferred_username'),
        'roles': user_info.get('realm_access', {}).get('roles', [])
    }
    return redirect(url_for('admin_dashboard'))
```

**Advantages:**
- Tokens never exposed to browser
- Server-controlled session management
- Enhanced audit capabilities
- Better for compliance requirements

**Trade-offs:**
- More server-side complexity
- Session state to manage
- Slightly slower page loads

## When to Choose Each Pattern

### Use Client-Side (SPA) When:
- Building modern, responsive user interfaces
- User experience is priority
- Development team has strong frontend skills
- Applications are primarily content consumption
- Acceptable security risk profile

### Use Server-Side When:
- Administrative or sensitive functions
- Compliance requirements mandate server-side sessions
- Need detailed audit trails
- Managing complex authorization logic
- Enhanced security is paramount

## Implementation Highlights

### Unified Keycloak Configuration

Both patterns use the same Keycloak realm with different clients:

```yaml
Realm: enterprise-sso
Clients:
  - internal-portal (Public client, SPA)
  - external-portal (Public client, SPA)  
  - admin-dashboard (Public client, Server-side)
```

Each client is configured with appropriate redirect URIs and back-channel logout URLs for seamless SSO experience.

### Enterprise Logout Coordination

One of the most complex aspects is ensuring logout works across different authentication patterns:

**Back-channel logout** from Keycloak notifies all applications server-to-server, while **front-channel logout** handles browser-based logout flows.

The SPA portals detect logout through:
```javascript
// Validate token on window focus
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

The server-side portal manages logout through global state tracking:
```python
# Global logout state (use Redis in production)
logged_out_users = set()

def require_auth(f):
    def decorated_function(*args, **kwargs):
        user_id = session.get('user', {}).get('id')
        if user_id in logged_out_users:
            session.clear()
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function
```

## Docker-Based Demo Environment

The complete implementation runs in Docker containers, making it easy to test both patterns:

```bash
# Start Keycloak
./start-keycloak.sh

# Start all portals
./start-portals.sh

# Access portals
# http://localhost:3001 - Internal Portal (SPA)
# http://localhost:3002 - External Portal (SPA)
# http://localhost:3003 - Admin Dashboard (Server-side)
```

## Real-World Considerations

### Security
- SPA pattern requires careful token handling and XSS protection
- Server-side pattern needs secure session storage (Redis in production)
- Both benefit from proper HTTPS and security headers

### Performance
- SPA patterns provide faster user interactions after initial load
- Server-side patterns have predictable performance characteristics
- Consider CDN and caching strategies for each

### Scalability  
- SPA patterns scale easily (stateless servers)
- Server-side patterns require session storage strategy
- Load balancing considerations differ between approaches

### Development Team
- SPA patterns require strong JavaScript/frontend skills
- Server-side patterns leverage traditional web development skills
- Consider team expertise when choosing patterns

## Next Steps

This multi-pattern approach gives you flexibility to choose the right authentication method for each application while maintaining unified SSO across your enterprise.

In **[Part 2]({{ site.baseurl }}{% post_url 2025-06-05-keycloak-spa-oidc-implementation %})** of this series, we'll dive deep into the SPA implementation details, including token management, OIDC client configuration, and handling edge cases like token expiration and network issues.

**[Part 3]({{ site.baseurl }}{% post_url 2025-06-05-keycloak-flask-server-side-authentication %})** will explore the server-side Flask implementation, covering session management, authorization middleware, and production deployment considerations.

## Source Code

The complete working implementation is available on GitHub: [keycloak-training](https://github.com/lucidprogrammer/keycloak-training)

The repository includes:
- Docker setup for Keycloak and all portals
- Complete source code for both authentication patterns
- Step-by-step configuration guide
- Production deployment considerations

---

*Need help implementing enterprise SSO for your organization? I provide Keycloak/IAM training and consulting services. Connect with me on [Upwork](https://www.upwork.com/fl/lucidp) or check out my other posts on enterprise authentication patterns.*