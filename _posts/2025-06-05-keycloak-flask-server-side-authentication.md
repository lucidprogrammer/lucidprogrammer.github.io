---
title: "Server-Side OIDC with Flask: Building Secure Admin Dashboards"
excerpt: "Deep dive into implementing server-side OpenID Connect authentication using Flask and Authlib, including session management, authorization middleware, and enterprise logout coordination."
categories:
  - Enterprise Authentication
  - SSO Implementation
tags:
  - keycloak
  - oidc
  - flask
  - authlib
  - server-side-authentication
  - python
  - session-management
date: 2025-06-05
toc: true
---

In our previous posts, we explored [architectural patterns]({{ site.baseurl }}{% post_url 2025-06-05-keycloak-sso-architecture-comparison %}) and [client-side SPA implementation]({{ site.baseurl }}{% post_url 2025-06-05-keycloak-spa-oidc-implementation %}). Now we'll examine server-side authentication using Flask and Authlib, focusing on the security and control benefits of traditional web application patterns.

## Why Server-Side Authentication?

While SPAs provide excellent user experience, administrative interfaces often require enhanced security, detailed audit trails, and centralized session control. Server-side authentication keeps sensitive tokens away from the browser and provides administrators with fine-grained control over user sessions.

## Flask Application Architecture

Our admin dashboard uses a hybrid Flask application that can serve multiple portal types based on command-line arguments:

```python
def parse_arguments():
    parser = argparse.ArgumentParser(description='Flask SSO Demo Portal Server')
    parser.add_argument('--portal', 
                      choices=['internal', 'external', 'admin'], 
                      default='internal',
                      help='Which portal to serve')
    parser.add_argument('--port', 
                      type=int, 
                      default=5000,
                      help='Port to run the server on')
    return parser.parse_args()
```

This design allows the same Flask application to serve different portal types while maintaining distinct authentication behaviors.

## OIDC Client Setup

### OAuth Configuration

We use Authlib's Flask integration for robust OIDC handling:

```python
from authlib.integrations.flask_client import OAuth
from authlib.common.errors import AuthlibBaseError

# Flask configuration
app.secret_key = secrets.token_urlsafe(32)

# OIDC Configuration
KEYCLOAK_SERVER_URL = 'http://localhost:8080'
KEYCLOAK_REALM = 'enterprise-sso'
KEYCLOAK_CLIENT_ID = 'admin-dashboard'

# Initialize OAuth
oauth = OAuth(app)

def setup_oidc_client():
    keycloak = oauth.register(
        'keycloak',
        client_id=KEYCLOAK_CLIENT_ID,
        client_secret=None,  # Public client
        authorize_url=f'{KEYCLOAK_SERVER_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/auth',
        access_token_url=f'{KEYCLOAK_SERVER_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token',
        userinfo_endpoint=f'{KEYCLOAK_SERVER_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/userinfo',
        jwks_uri=f'{KEYCLOAK_SERVER_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/certs',
        client_kwargs={
            'scope': 'openid profile email'
        }
    )
    return keycloak
```

Note that we're manually configuring endpoints rather than using discovery. This provides explicit control and can help with debugging OIDC flow issues.

## Authentication Middleware

### Authorization Decorator

The core of our server-side security is the `require_auth` decorator:

```python
def require_auth(f):
    def decorated_function(*args, **kwargs):
        logger.info(f"🔍 require_auth check: session keys = {list(session.keys())}")
        
        # Check if user exists in session
        if 'user' not in session:
            logger.info(f"🔒 No user in session, redirecting to login")
            return redirect(url_for('admin_login'))
        
        # Check if user has been logged out globally
        user_id = session.get('user', {}).get('id')
        if user_id in logged_out_users or '*' in logged_out_users:
            logger.info(f"🔒 User {user_id} has been logged out globally, clearing session")
            session.clear()
            return redirect(url_for('admin_login'))
        
        logger.info(f"✅ User authenticated: {session.get('user', {}).get('username', 'unknown')}")
        return f(*args, **kwargs)
    
    decorated_function.__name__ = f.__name__
    return decorated_function
```

This decorator handles both session validation and global logout state checking, essential for enterprise SSO environments.

### Global Logout State Management

For demonstration purposes, we use an in-memory set to track logged-out users:

```python
# Global logout tracking (use Redis in production)
logged_out_users = set()
```

In production, this should be replaced with Redis or a database to ensure logout state persists across application restarts and scales across multiple server instances.

## Authentication Flow Implementation

### Login Page Route

```python
@app.route('/admin/login')
def admin_login():
    if PORTAL_TYPE != 'admin':
        return "Not available on this portal", 404
    
    if 'user' in session:
        return redirect(url_for('admin_dashboard'))
    
    return render_template('login.html', portal_info=PORTAL_INFO)
```

### OIDC Authorization Initiation

```python
@app.route('/admin/auth')
def admin_auth():
    if PORTAL_TYPE != 'admin':
        return "Not available on this portal", 404
    
    redirect_uri = url_for('admin_callback', _external=True)
    return keycloak_client.authorize_redirect(redirect_uri)
```

The `authorize_redirect()` method constructs the proper OIDC authorization URL and redirects to Keycloak, similar to the SPA implementation but handled server-side.

### Authorization Callback Handling

The callback route processes the authorization code and exchanges it for tokens:

```python
@app.route('/admin/callback')
def admin_callback():
    if PORTAL_TYPE != 'admin':
        return "Not available on this portal", 404
    
    try:
        token = keycloak_client.authorize_access_token()
        user_info = token.get('userinfo')
        
        if user_info:
            session['user'] = {
                'id': user_info.get('sub'),
                'username': user_info.get('preferred_username'),
                'email': user_info.get('email'),
                'name': user_info.get('name', user_info.get('preferred_username')),
                'roles': user_info.get('realm_access', {}).get('roles', [])
            }
            session['access_token'] = token.get('access_token')
            logger.info(f"✅ Admin user logged in: {session['user']['username']}")
            
            # Remove user from logged out list (in case of re-login)
            user_id = session['user']['id']
            logged_out_users.discard(user_id)
            logged_out_users.discard('*')
            
            return redirect(url_for('admin_dashboard'))
        else:
            return render_template('error.html', 
                                 error="Authentication failed - no user information received",
                                 portal_info=PORTAL_INFO)
    
    except AuthlibBaseError as e:
        logger.error(f"OIDC authentication error: {e}")
        return render_template('error.html', 
                             error=f"Authentication failed: {str(e)}",
                             portal_info=PORTAL_INFO)
```

Key points:
- Tokens are stored in server-side sessions, never exposed to the browser
- User information is extracted and stored in a structured format
- Error handling provides useful feedback for debugging
- Global logout state is cleared on successful login

## Dashboard Implementation

### Protected Dashboard Route

```python
@app.route('/admin/dashboard')
@require_auth
def admin_dashboard():
    if PORTAL_TYPE != 'admin':
        return "Not available on this portal", 404
    
    user = session.get('user')
    is_admin = 'admin' in user.get('roles', []) or 'approver' in user.get('roles', [])
    
    # Mock data for demo
    stats = {
        'pending_approvals': 23,
        'active_users': 156,
        'connected_systems': 8,
        'system_uptime': '99.8%'
    }
    
    pending_items = [
        {'type': 'Leave Request', 'description': 'John Doe - Annual Leave (3 days)'},
        {'type': 'Purchase Order', 'description': 'IT Equipment - ฿125,000'},
        {'type': 'Vendor Registration', 'description': 'ABC Consulting Co.'},
        {'type': 'Project Budget', 'description': 'Digital Transformation Phase 2'}
    ]
    
    return render_template('dashboard.html', 
                         user=user, 
                         is_admin=is_admin,
                         stats=stats,
                         pending_items=pending_items,
                         portal_info=PORTAL_INFO)
```

The dashboard demonstrates role-based access control and passes structured data to the Jinja2 template for rendering.

## Template System

### Base Template Structure

```html
{% raw %}
<!-- base.html -->
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}{{ portal_info.name }}{% endblock %}</title>
    <link rel="stylesheet" href="{{ url_for('static', filename='admin.css') }}">
</head>
<body>
    <div class="header">
        <h1>{{ portal_info.icon }} {{ portal_info.name }}</h1>
        <p>{{ portal_info.description }}</p>
        {% if user %}
        <div class="user-badge">
            <span>👤 {{ user.name or user.username }}</span>
            <a href="{{ url_for('admin_logout') }}" class="btn btn-danger btn-sm">Logout</a>
        </div>
        {% endif %}
    </div>

    <div class="container">
        {% block content %}{% endblock %}
    </div>

    {% block scripts %}{% endblock %}
</body>
</html>
{% endraw %}
```

### Dashboard Template

The dashboard template extends the base template and displays user information and administrative functions:

```html
{% raw %}
<!-- dashboard.html -->
{% extends "base.html" %}

{% block content %}
<div class="dashboard-content">
    <h2>Administrative Control Panel</h2>
    
    <div class="user-info">
        <h3>Administrator Information</h3>
        <div class="info-grid">
            <div class="info-item">
                <label>Administrator:</label>
                <span>{{ user.username }}</span>
            </div>
            <div class="info-item">
                <label>Session Type:</label>
                <span>🖥️ Server-Side Session</span>
            </div>
            <div class="info-item">
                <label>Permissions:</label>
                <span>{{ user.roles | join(', ') if user.roles else 'employee' }}</span>
            </div>
        </div>
    </div>

    <!-- Dynamic content based on user roles and data -->
    {% for item in pending_items %}
    <div class="approval-item">
        <div class="approval-type">{{ item.type }}</div>
        <div class="approval-desc">{{ item.description }}</div>
        <div class="approval-actions">
            <button class="btn btn-success btn-sm">✅ Approve</button>
            <button class="btn btn-warning btn-sm">⏳ Review</button>
            <button class="btn btn-danger btn-sm">❌ Reject</button>
        </div>
    </div>
    {% endfor %}
</div>
{% endblock %}
{% endraw %}
```

## Enterprise Logout Implementation

### Logout Coordination

Server-side logout requires coordination between front-channel and back-channel logout flows:

```python
@app.route('/logout.html', methods=['GET', 'POST'])
@app.route('/logout', methods=['GET', 'POST'])
def logout():
    if request.method == 'POST':
        # Back-channel logout from Keycloak
        logger.info(f"🔴 BACK-CHANNEL LOGOUT received from Keycloak at {PORTAL_INFO['name']}")
        
        # Parse logout token to get user info (if available)
        logout_token = request.form.get('logout_token')
        if logout_token:
            logger.info(f"📝 Logout token received: {logout_token[:50]}...")
        
        # Add current session user to logged out users
        current_user_id = session.get('user', {}).get('id')
        if current_user_id:
            logged_out_users.add(current_user_id)
            logger.info(f"🗑️ Added user {current_user_id} to global logout list")
        
        # For demo, also add wildcard to logout all current users
        logged_out_users.add('*')
        
        # Clear the current session
        session.clear()
        logger.info(f"✅ Admin session cleared via back-channel logout")
        
        return "Logout acknowledged", 200
    
    else:  # GET request - front-channel logout
        logger.info(f"🟡 FRONT-CHANNEL LOGOUT received at {PORTAL_INFO['name']}")
        
        if 'user' in session:
            user = session.get('user')
            logger.info(f"🗑️ Clearing admin session for: {user.get('username', 'unknown')}")
        
        session.clear()
        logger.info(f"✅ Admin session cleared via front-channel logout")
        return redirect(url_for('admin_login'))
```

### Admin-Initiated Logout

```python
@app.route('/admin/logout')
def admin_logout():
    if PORTAL_TYPE != 'admin':
        return "Not available on this portal", 404
    
    if 'user' in session:
        user = session['user']
        logger.info(f"🔴 Admin user logout initiated: {user['username']}")
        
        # Clear session
        session.clear()
        
        # Redirect to Keycloak logout
        logout_url = f"{KEYCLOAK_SERVER_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/logout?" \
                    f"post_logout_redirect_uri={url_for('admin_login', _external=True)}"
        
        return redirect(logout_url)
    else:
        return redirect(url_for('admin_login'))
```

## Production Considerations

### Session Security

```python
# Production session configuration
app.config.update(
    SESSION_COOKIE_SECURE=True,  # HTTPS only
    SESSION_COOKIE_HTTPONLY=True,  # No JavaScript access
    SESSION_COOKIE_SAMESITE='Lax',  # CSRF protection
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8)  # Session timeout
)
```

### Redis Session Storage

For production deployments, use Redis for session storage:

```python
import redis
from flask_session import Session

# Redis configuration
app.config['SESSION_TYPE'] = 'redis'
app.config['SESSION_REDIS'] = redis.from_url('redis://localhost:6379')
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_USE_SIGNER'] = True
app.config['SESSION_KEY_PREFIX'] = 'admin:'

Session(app)
```

### Global Logout State with Redis

```python
import redis

logout_redis = redis.from_url('redis://localhost:6379', db=1)

def is_user_logged_out(user_id):
    return logout_redis.exists(f"logout:{user_id}") or logout_redis.exists("logout:*")

def mark_user_logged_out(user_id, ttl=3600):
    logout_redis.setex(f"logout:{user_id}", ttl, "1")

def clear_logout_state(user_id):
    logout_redis.delete(f"logout:{user_id}")
    logout_redis.delete("logout:*")
```

### Error Handling and Monitoring

```python
import logging
from flask import request

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s %(message)s'
)

@app.before_request
def log_request_info():
    logger.info(f"Request: {request.method} {request.url}")
    if 'user' in session:
        logger.info(f"User: {session['user']['username']}")

@app.after_request
def log_response_info(response):
    logger.info(f"Response: {response.status_code}")
    return response
```

## Docker Integration

### Multi-Portal Flask Application

The Flask application dynamically serves different portal types:

```python
def main():
    global STATIC_DIR, PORTAL_TYPE, PORTAL_INFO, keycloak_client
    
    args = parse_arguments()
    PORTAL_TYPE = args.portal
    PORTAL_INFO = get_portal_info(PORTAL_TYPE)
    STATIC_DIR = get_static_directory(PORTAL_TYPE)
    
    # Setup OIDC client for admin portal
    if PORTAL_TYPE == 'admin':
        keycloak_client = setup_oidc_client()
        # Set Flask template folder for admin portal
        app.template_folder = os.path.join(STATIC_DIR, 'templates')
        app.static_folder = os.path.join(STATIC_DIR, 'static')
    
    logger.info(f"🚀 Starting {PORTAL_INFO['icon']} {PORTAL_INFO['name']}")
    app.run(host='0.0.0.0', port=args.port, debug=False)
```

### Container Orchestration

```bash
# Start Admin Dashboard
docker run -d \
  --name enterprise-admin \
  --network=host \
  lucidprogrammer/keycloak-training \
  python app.py --portal=admin --port=3003
```

The admin portal uses host networking to ensure proper Keycloak connectivity, while SPA portals use bridge networking with port mapping.

## Comparison: Server-Side vs Client-Side

### Security Model

**Server-Side Advantages:**
- Tokens never exposed to browser
- Centralized session management
- Enhanced audit capabilities
- Protection against XSS token theft

**Client-Side Advantages:**
- No server-side session state
- Better scalability
- Modern user experience
- Reduced server complexity

### Development Complexity

**Server-Side:**
```python
# Simple route protection
@app.route('/dashboard')
@require_auth
def dashboard():
    return render_template('dashboard.html', user=session['user'])
```

**Client-Side:**
```javascript
// More complex state management
const user = await userManager.getUser();
if (user && !user.expired) {
    const isValid = await validateTokenWithServer(user);
    // Handle various states...
}
```

### Operational Considerations

**Server-Side:**
- Session storage requirements (Redis)
- Server-side scaling considerations
- Traditional monitoring and logging

**Client-Side:**
- Stateless server applications
- CDN-friendly static assets
- Browser-based debugging required

## Testing Strategies

### Integration Testing

```python
import pytest
from flask import session

def test_admin_login_flow(client, keycloak_mock):
    # Test login redirect
    response = client.get('/admin/auth')
    assert response.status_code == 302
    assert 'keycloak' in response.location
    
    # Test callback handling
    with client.session_transaction() as sess:
        # Mock successful callback
        response = client.get('/admin/callback?code=test_code')
        assert response.status_code == 302
        assert response.location.endswith('/admin/dashboard')

def test_logout_coordination(client):
    # Test back-channel logout
    response = client.post('/logout', data={'logout_token': 'test_token'})
    assert response.status_code == 200
    assert response.data == b'Logout acknowledged'
```

## Next Steps and Production Deployment

This server-side implementation provides a solid foundation for production administrative interfaces. Key considerations for scaling:

1. **Session Storage**: Migrate to Redis or database-backed sessions
2. **Load Balancing**: Ensure session affinity or shared session storage
3. **Monitoring**: Implement comprehensive logging and metrics
4. **Security Headers**: Add CSRF protection, security headers
5. **Rate Limiting**: Protect against brute force attacks

## Source Code

The complete Flask implementation is available in the [keycloak-training repository](https://github.com/lucidprogrammer/keycloak-training):

- `app.py` - Main Flask application with hybrid portal support
- `admin-dashboard/templates/` - Jinja2 templates
- `admin-dashboard/static/` - CSS and JavaScript assets
- `Dockerfile` - Container configuration

## Series Conclusion

This three-part series has explored enterprise SSO implementation from architectural decisions through detailed implementation patterns. The choice between client-side and server-side authentication depends on your specific security requirements, user experience goals, and operational constraints.

Both patterns can coexist within the same Keycloak realm, allowing you to choose the optimal approach for each application while maintaining unified SSO across your enterprise.

---

*Need expert guidance on implementing enterprise authentication systems? I provide specialized Keycloak training and consulting services. Connect with me on [Upwork](https://www.upwork.com/fl/lucidp) to discuss your SSO implementation requirements.*