---
title: "Enterprise Identity Federation with Keycloak and LDAP: Connecting Existing User Directories"
excerpt: "A practical guide to integrating existing LDAP directories with Keycloak for seamless SSO, including OpenLDAP setup, user synchronization, and role-based access control."
categories:
  - Enterprise Authentication
  - Identity Federation
tags:
  - keycloak
  - ldap
  - openldap
  - identity-federation
  - user-synchronization
  - enterprise-directory
date: 2025-06-05
toc: true
---

When implementing enterprise SSO, one of the biggest challenges is integrating with existing user directories. Organizations often have years of user data, groups, and permissions stored in LDAP or Active Directory systems. Rather than recreating this infrastructure, Keycloak's federation capabilities allow you to connect existing user stores while adding modern authentication features.

This post builds on our [multi-architecture SSO series]({{ site.baseurl }}{% post_url 2025-06-05-keycloak-sso-architecture-comparison %}), showing how to integrate existing user directories with the [SPA]({{ site.baseurl }}{% post_url 2025-06-05-keycloak-spa-oidc-implementation %}) and [server-side]({{ site.baseurl }}{% post_url 2025-06-05-keycloak-flask-server-side-authentication %}) authentication patterns we've already implemented.

In this guide, I'll walk through setting up LDAP federation with Keycloak, demonstrating how to preserve existing user management workflows while enabling modern SSO capabilities across all portal types.

## Why Federate Identity Sources?

### The Problem with User Recreation

Many SSO implementations fail because they require:
- Recreating all users in the new system
- Duplicate user management processes
- Loss of existing group structures and permissions
- User confusion with new credentials

### Federation Benefits

**Preserve Existing Infrastructure:**
- Keep current LDAP/Active Directory systems
- Maintain existing user provisioning workflows
- Leverage established group structures

**Add Modern Capabilities:**
- OpenID Connect and OAuth2 support
- Multi-factor authentication
- Social login integration
- Modern web and mobile app support

**Unified Experience:**
- Single sign-on across old and new applications
- Centralized session management
- Consistent user experience

## Federation Architecture Overview

Our demonstration setup includes:

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   OpenLDAP      │    │    Keycloak     │    │  Applications   │
│   Directory     │◄───┤   Federation    │◄───┤  (3 Portals)    │
│                 │    │     Layer       │    │                 │
│ • Users         │    │ • User Sync     │    │ • Internal      │
│ • Groups        │    │ • Role Mapping  │    │ • External      │
│ • Attributes    │    │ • SSO Sessions  │    │ • Admin         │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

**Flow:**
1. Users authenticate against Keycloak
2. Keycloak federates authentication to LDAP
3. User attributes and groups sync from LDAP
4. Applications receive standard OIDC tokens

## LDAP vs Active Directory: Enterprise Directory Options

Before diving into implementation, it's important to understand the relationship between LDAP and Active Directory:

### LDAP (Lightweight Directory Access Protocol)
- **Protocol standard** for accessing and maintaining distributed directory information
- **Open standard** implemented by various vendors (OpenLDAP, 389 Directory Server, etc.)
- **Cross-platform** compatibility and vendor flexibility

### Microsoft Active Directory (AD)
- **Microsoft's implementation** of LDAP protocol with proprietary extensions
- **Windows-integrated** with additional features like Group Policy, DNS integration
- **Enterprise standard** in many Windows-based organizations

### Keycloak Federation Compatibility

**The good news:** Keycloak's LDAP federation works with both:

```yaml
LDAP Servers Supported:
  - Microsoft Active Directory
  - OpenLDAP  
  - 389 Directory Server
  - Apache Directory Server
  - Oracle Internet Directory
  - IBM Security Directory Server
```

**Configuration differences are minimal:**

| Feature | OpenLDAP | Active Directory |
|---------|----------|-----------------|
| Connection URL | `ldap://server:389` | `ldap://domain.com:389` |
| Bind DN | `cn=admin,dc=company,dc=com` | `user@domain.com` or `DOMAIN\user` |
| User Object Classes | `inetOrgPerson,posixAccount` | `user,person,organizationalPerson` |
| Group Mapping | `groupOfNames` | `group` |

**For this tutorial:** We use OpenLDAP because it's easier to set up for training, but the same principles apply to Active Directory environments.

## OpenLDAP Setup for Testing

### Docker-Based LDAP Server

For demonstration purposes, we'll use a containerized OpenLDAP server:

```bash
# Start OpenLDAP server with test users
docker run -d --name openldap-server \
  -p 1389:1389 \
  -e LDAP_ADMIN_USERNAME=admin \
  -e LDAP_ADMIN_PASSWORD=adminpassword \
  -e LDAP_ROOT=dc=enterprise,dc=local \
  -e LDAP_USERS=john.doe,jane.smith,mike.admin \
  -e LDAP_PASSWORDS=password123,password123,password123 \
  bitnami/openldap:latest
```

### Verify LDAP Structure

```bash
# Check that users were created successfully
docker exec -it openldap-server ldapsearch \
  -x -H ldap://localhost:1389 \
  -D "cn=admin,dc=enterprise,dc=local" \
  -w adminpassword \
  -b "dc=enterprise,dc=local"
```

**Expected Structure:**
```
dc=enterprise,dc=local
├── ou=users
│   ├── cn=john.doe
│   ├── cn=jane.smith
│   └── cn=mike.admin
└── cn=admin
```

### Test Users Created

The Docker setup creates three LDAP users (roles will be assigned manually in Keycloak):

| Username | Password | Intended Role |
|----------|----------|---------------|
| john.doe | password123 | Employee (to be assigned) |
| jane.smith | password123 | External Partner (to be assigned) |
| mike.admin | password123 | Administrator (to be assigned) |

**Note:** The Docker command only creates users in LDAP. Roles will be manually assigned in Keycloak during the demonstration.

## Keycloak LDAP Federation Configuration

### Step 1: Create LDAP User Federation Provider

In Keycloak Admin Console:

1. **Navigate to User Federation**
2. **Select "Add provider" → "ldap"**
3. **Configure connection settings:**

```yaml
Provider Settings:
  Console Display Name: "Enterprise LDAP"
  Priority: 0
  
Connection Settings:
  Connection URL: "ldap://localhost:1389"
  Bind Type: "Simple"
  Bind DN: "cn=admin,dc=enterprise,dc=local"
  Bind Credential: "adminpassword"
  
LDAP Searching and Updating:
  Edit Mode: "READ_ONLY"
  Users DN: "ou=users,dc=enterprise,dc=local"
  Username LDAP Attribute: "uid"
  RDN LDAP Attribute: "cn"
  UUID LDAP Attribute: "uid"
  User Object Classes: "inetOrgPerson,posixAccount,shadowAccount"
```

### Step 2: Test Connection

Before saving the configuration:

1. **Click "Test Connection"** - should show success
2. **Click "Test Authentication"** - should authenticate as admin
3. **Save** the configuration

### Step 3: User Synchronization

```bash
# In Keycloak Admin Console
User Federation → ldap → "Synchronize all users"
```

**For Active Directory environments, the configuration would be:**

```yaml
# Active Directory equivalent configuration
Connection URL: "ldap://ad.company.com:389"
Bind Type: "Simple"  
Bind DN: "ldap-service@company.com"  # or COMPANY\ldap-service
Bind Credential: "service-account-password"
Users DN: "CN=Users,DC=company,DC=com"
Username LDAP Attribute: "sAMAccountName"  # AD-specific
RDN LDAP Attribute: "cn"
UUID LDAP Attribute: "objectGUID"  # AD-specific
User Object Classes: "user,person,organizationalPerson"
```

## Attribute Mapping Configuration

### Default Mappers

Keycloak automatically creates attribute mappers for LDAP federation:

| Mapper Name | LDAP Attribute | Keycloak Attribute | Purpose |
|-------------|----------------|-------------------|---------|
| email | mail | email | User email address |
| full name | cn | firstName + lastName | Display name |
| username | uid | username | Login identifier |
| last name | sn | lastName | Surname |

### Custom Attribute Mapping

For additional attributes, create custom mappers:

```yaml
Department Mapper:
  Name: "department"
  Mapper Type: "user-attribute-ldap-mapper"
  User Model Attribute: "department"
  LDAP Attribute: "departmentNumber"
  Read Only: ON
  Always Read Value From LDAP: ON
```

## Role-Based Access Implementation

### Manual Role Assignment (Training Approach)

Since our OpenLDAP setup creates users without group membership, we'll manually assign roles to demonstrate role-based access:

**Step 1: Create Roles in Keycloak**
1. Go to **Realm Settings → Roles**
2. Click **"Create Role"**
3. Create these roles:
   - `employee` - Basic user access
   - `admin` - Administrative access
   - `external` - Partner/vendor access

**Step 2: Assign Roles to Federated Users**
1. **Go to Users → john.doe → Role Mapping**
2. **Assign role:** `employee`
3. **Go to Users → mike.admin → Role Mapping**  
4. **Assign role:** `admin`
5. **Go to Users → jane.smith → Role Mapping**
6. **Assign role:** `external`

**Note:** In production environments, roles would typically come from LDAP groups or Active Directory group membership through group-to-role mappers.

### Portal Integration with Roles

Update your portal applications to respect user roles:

```javascript
// In SPA portals (internal-portal/index.html)
function showUserSection(user) {
    hideStatus();
    document.getElementById('login-section').style.display = 'none';
    document.getElementById('user-section').style.display = 'block';
    
    // Basic user information
    document.getElementById('user-details').innerHTML = `
        <p><strong>Username:</strong> ${user.profile.preferred_username || 'N/A'}</p>
        <p><strong>Email:</strong> ${user.profile.email || 'N/A'}</p>
        <p><strong>Name:</strong> ${user.profile.name || user.profile.preferred_username || 'N/A'}</p>
        <p><strong>Roles:</strong> ${user.profile.realm_access?.roles?.join(', ') || 'employee'}</p>
        <p><strong>Source:</strong> 🔗 Federated from LDAP</p>
    `;
    
    // Role-based feature display
    const isAdmin = user.profile.realm_access?.roles?.includes('admin');
    
    if (isAdmin) {
        document.getElementById('user-details').innerHTML += `
            <div style="background-color: #ffe6e6; padding: 15px; margin: 15px 0; border-radius: 4px; border-left: 4px solid #dc3545;">
                <h4>🔧 Administrator Features</h4>
                <ul>
                    <li>User Management & Provisioning</li>
                    <li>System Configuration</li>
                    <li>Audit Logs & Compliance Reports</li>
                    <li>Federation Settings</li>
                </ul>
            </div>
        `;
    }
}
```

### Server-Side Role Enforcement

```python
# In Flask admin dashboard (app.py)
@app.route('/admin/dashboard')
@require_auth
def admin_dashboard():
    user = session.get('user')
    is_admin = 'admin' in user.get('roles', [])
    
    if not is_admin:
        return render_template('error.html', 
                             error="Access denied - Administrator role required. Please contact your LDAP administrator.",
                             portal_info=PORTAL_INFO)
    
    # Enhanced stats for federated environment
    stats = {
        'pending_approvals': 23,
        'active_users': 156,
        'connected_systems': 8,
        'federated_sources': 2,  # LDAP + local users
        'system_uptime': '99.8%'
    }
    
    return render_template('dashboard.html', 
                         user=user, 
                         is_admin=is_admin,
                         stats=stats,
                         pending_items=pending_items,
                         portal_info=PORTAL_INFO)
```

## Testing Federation Integration

### 1. LDAP User Authentication Test

```bash
# Test sequence for training demonstration
echo "Testing LDAP Federation..."

echo "1. Verify LDAP users exist:"
docker exec -it openldap-server ldapsearch -x -H ldap://localhost:1389 -D "cn=admin,dc=enterprise,dc=local" -w adminpassword -b "ou=users,dc=enterprise,dc=local" "(objectClass=inetOrgPerson)"

echo "2. Check Keycloak user sync:"
# Manual check: Keycloak Admin → Users → should show LDAP users

echo "3. Test authentication flow:"
# Manual test: Login to portals with LDAP credentials
```

### 2. Cross-Portal SSO Verification

**Test Flow:**
1. **Login to Internal Portal** with `john.doe` / `password123`
2. **Navigate to External Portal** - should auto-login via SSO
3. **Navigate to Admin Dashboard** - should show access denied for non-admin
4. **Logout from any portal** - should logout from all

### 3. Role-Based Access Testing

**Employee User Test (john.doe):**
```bash
# Expected behavior:
# ✅ Can login to Internal Portal
# ✅ Can login to External Portal  
# ❌ Cannot access Admin Dashboard
# ✅ SSO works across accessible portals
```

**Admin User Test (mike.admin):**
```bash
# Expected behavior:
# ✅ Can login to all portals
# ✅ Sees admin features in portals
# ✅ Full access to Admin Dashboard
# ✅ SSO works across all portals
```

## Production Considerations

### Security Best Practices

**Connection Security:**
```yaml
# Production LDAP configuration
Connection URL: "ldaps://ldap.enterprise.local:636"  # Use LDAPS
Bind Credential: "****"  # Store in secure credential store
Use Truststore SPI: "Only for trusted hosts"
Connection Pooling: "ON"
```

**Access Controls:**
```yaml
# Restrict LDAP read access
Edit Mode: "READ_ONLY"  # Prevent Keycloak from modifying LDAP
Import Users: "ON"  # Cache users in Keycloak database
Sync Registrations: "OFF"  # Prevent new user creation
```

### Performance Optimization

**Synchronization Strategy:**
```yaml
# Periodic sync configuration
Sync Settings:
  Periodic Full Sync: "ON"
  Full Sync Period: "86400"  # Once daily
  Periodic Changed Users Sync: "ON"  
  Changed Users Sync Period: "3600"  # Hourly
```

**Caching Configuration:**
```yaml
# User caching for performance
Cache Policy: "DEFAULT"
Max Lifespan: "3600000"  # 1 hour
Eviction Hour: "2"  # Sync at 2 AM
Eviction Minute: "0"
Eviction Day: "Sunday"
```

### Monitoring and Troubleshooting

**Common Issues:**

**Connection Problems:**
```bash
# Debug LDAP connectivity
ldapsearch -x -H ldap://ldap-server:389 -D "bind-dn" -w "password" -b "base-dn"

# Check Keycloak logs
docker logs keycloak-training | grep -i ldap
```

**Synchronization Issues:**
```bash
# Force manual sync
# Keycloak Admin → User Federation → ldap → "Synchronize all users"

# Check sync status
# Look for errors in Keycloak server logs
```

**Authentication Failures:**
```yaml
# Verify configuration
- Check bind DN and credentials
- Verify users DN path
- Confirm user object classes
- Test with LDAP browser tool
```

### High Availability Setup

**Multiple LDAP Servers:**
```yaml
# Primary LDAP configuration
Connection URL: "ldap://ldap1.enterprise.local:389 ldap://ldap2.enterprise.local:389"
Connection Pooling: "ON"
Connection Timeout: "10000"
Read Timeout: "30000"
```

## Advanced Federation Patterns

### Multi-Source Federation

Organizations often have multiple user stores:

```yaml
Federation Sources:
  1. Active Directory: Internal employees
  2. OpenLDAP: External partners  
  3. Database: Service accounts
  4. Social Providers: Customer accounts
```

**Implementation Strategy:**
- Configure separate federation providers
- Use different realms or identity provider mappers
- Implement user source priority rules

### Attribute Enrichment

Combine attributes from multiple sources:

```python
# Example: Enrich LDAP users with application-specific attributes
@app.route('/api/user/profile')
@require_auth
def get_user_profile():
    user = session.get('user')
    
    # Base attributes from LDAP
    profile = {
        'username': user['username'],
        'email': user['email'],
        'source': 'ldap'
    }
    
    # Enrich with application-specific data
    app_data = get_application_data(user['username'])
    profile.update(app_data)
    
    return jsonify(profile)
```

## Migration Strategy

### Phased Approach

**Phase 1: Federation Setup**
- Configure LDAP federation
- Test with pilot user group
- Validate attribute mapping

**Phase 2: Application Integration**
- Update applications to use Keycloak
- Implement role-based access controls
- Test SSO functionality

**Phase 3: Full Deployment**
- Migrate all applications
- Decommission legacy authentication
- Monitor and optimize performance

### Rollback Planning

```yaml
# Maintain dual authentication during migration
Authentication Options:
  1. Keycloak SSO (new)
  2. Direct LDAP (legacy fallback)
  3. Local accounts (emergency access)
```

## Source Code Integration

The complete federation setup integrates with our existing portal demonstration:

**Repository Updates:**
- LDAP federation configuration guide
- Updated portal code with role-based features
- Docker compose for complete environment
- Testing scripts and verification procedures

**Available at:** [keycloak-training](https://github.com/lucidprogrammer/keycloak-training)

**New Features:**
- LDAP integration documentation
- Role-based UI components
- Federation testing utilities
- Production deployment considerations

## Conclusion

LDAP federation with Keycloak provides a powerful bridge between existing enterprise directories and modern authentication systems. By preserving existing user management workflows while adding contemporary SSO capabilities, organizations can modernize authentication without disrupting established processes.

Key benefits realized:
- **Preserve Investment**: Leverage existing LDAP infrastructure
- **Reduce Complexity**: Single authentication source for all applications
- **Improve Security**: Centralized session management and modern protocols
- **Enable Innovation**: Foundation for adding MFA, social login, and mobile support

The federation approach demonstrates that enterprise SSO implementation doesn't require ripping out existing systems - it can enhance and modernize them while maintaining operational continuity.

This completes our enterprise SSO implementation series, covering [architectural decisions]({{ site.baseurl }}{% post_url 2025-06-05-keycloak-sso-architecture-comparison %}), [client-side implementation]({{ site.baseurl }}{% post_url 2025-06-05-keycloak-spa-oidc-implementation %}), [server-side patterns]({{ site.baseurl }}{% post_url 2025-06-05-keycloak-flask-server-side-authentication %}), and now identity federation. Together, these posts provide a comprehensive foundation for implementing production-ready enterprise authentication systems.

---

*Implementing enterprise identity federation for your organization? I provide specialized Keycloak training and consulting services, including LDAP integration, migration planning, and production deployment. Connect with me on [Upwork](https://www.upwork.com/fl/lucidp) to discuss your identity federation requirements.*