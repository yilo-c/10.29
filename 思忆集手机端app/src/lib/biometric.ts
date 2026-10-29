const RP_NAME = '思忆便签'
const RP_ID = window.location.hostname || 'localhost'

/**
 * Check if platform biometric auth is available (Face ID / Touch ID / fingerprint).
 */
export async function isBiometricAvailable(): Promise<boolean> {
  if (!window.PublicKeyCredential) return false
  try {
    const available = await PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable()
    return available
  } catch {
    return false
  }
}

/**
 * Register a new biometric credential for the given userId.
 * Stores the credential ID in localStorage so it can be used for later auth.
 * Returns true if registration was successful.
 */
export async function registerBiometric(userId: string): Promise<boolean> {
  if (!window.PublicKeyCredential) return false

  try {
    const challenge = crypto.getRandomValues(new Uint8Array(32))

    const credential = await navigator.credentials.create({
      publicKey: {
        challenge,
        rp: { name: RP_NAME, id: RP_ID },
        user: {
          id: new TextEncoder().encode(userId),
          name: userId,
          displayName: '思忆便签用户',
        },
        pubKeyCredParams: [
          { type: 'public-key', alg: -7 },   // ES256
          { type: 'public-key', alg: -257 },  // RS256
        ],
        authenticatorSelection: {
          authenticatorAttachment: 'platform',
          userVerification: 'required',
          residentKey: 'preferred',
        },
        timeout: 30000,
      },
    })

    if (credential) {
      const credId = (credential as PublicKeyCredential).id
      localStorage.setItem('siyi-biometric-cred', credId)
      return true
    }
    return false
  } catch {
    return false
  }
}

/**
 * Authenticate using a previously registered biometric credential.
 * Returns true if authentication succeeded.
 */
export async function authenticateBiometric(): Promise<boolean> {
  const credId = localStorage.getItem('siyi-biometric-cred')
  if (!credId || !window.PublicKeyCredential) return false

  try {
    const challenge = crypto.getRandomValues(new Uint8Array(32))

    const assertion = await navigator.credentials.get({
      publicKey: {
        challenge,
        allowCredentials: [{
          id: base64urlToUint8Array(credId) as unknown as BufferSource,
          type: 'public-key',
          transports: ['internal'],
        }],
        userVerification: 'required',
        timeout: 30000,
      },
    })

    return !!assertion
  } catch {
    return false
  }
}

/**
 * Remove stored biometric credential.
 */
export function removeBiometricCredential() {
  localStorage.removeItem('siyi-biometric-cred')
}

function base64urlToUint8Array(base64url: string): Uint8Array {
  const base64 = base64url.replace(/-/g, '+').replace(/_/g, '/')
  const binary = atob(base64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i)
  }
  return bytes
}
