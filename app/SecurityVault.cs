using System;
using System.IO;
using System.Security.Cryptography;
using System.Text;

namespace KZManager;

/// <summary>
/// 26. 本地数据保险箱 (Master Password Vault)。
/// 引入主密码保护机制，采用 AES-GCM-256 + PBKDF2 (100,000轮哈希)
/// 对敏感节点密码、账号凭证与 profile 敏感数据进行保护。
/// </summary>
public static class SecurityVault
{
    static byte[]? _masterKey;
    static readonly byte[] FixedSalt = Encoding.UTF8.GetBytes("KZManager.Vault.Salt.2026");

    public static bool IsLocked => _masterKey is null;

    public static bool SetMasterPassword(string password)
    {
        if (string.IsNullOrEmpty(password) || password.Length < 6) return false;
        _masterKey = Rfc2898DeriveBytes.Pbkdf2(password, FixedSalt, 100_000, HashAlgorithmName.SHA256, 32);
        return true;
    }

    public static void Lock()
    {
        if (_masterKey is not null)
        {
            CryptographicOperations.ZeroMemory(_masterKey);
            _masterKey = null;
        }
    }

    /// <summary>AES-GCM-256 加密字符串，返回 Base64。</summary>
    public static string Encrypt(string plaintext)
    {
        if (string.IsNullOrEmpty(plaintext)) return "";
        if (_masterKey is null) return plaintext; // 未上锁时保留明文

        var plainBytes = Encoding.UTF8.GetBytes(plaintext);
        var nonce = RandomNumberGenerator.GetBytes(12);
        var tag = new byte[16];
        var cipher = new byte[plainBytes.Length];

        using var aes = new AesGcm(_masterKey, 16);
        aes.Encrypt(nonce, plainBytes, cipher, tag);

        // 打包: [nonce(12)][tag(16)][cipher(N)]
        var packed = new byte[12 + 16 + cipher.Length];
        Buffer.BlockCopy(nonce, 0, packed, 0, 12);
        Buffer.BlockCopy(tag, 0, packed, 12, 16);
        Buffer.BlockCopy(cipher, 0, packed, 28, cipher.Length);

        return "VAULT:" + Convert.ToBase64String(packed);
    }

    /// <summary>AES-GCM-256 解密 Base64 密文。</summary>
    public static string Decrypt(string ciphertext)
    {
        if (string.IsNullOrEmpty(ciphertext)) return "";
        if (!ciphertext.StartsWith("VAULT:")) return ciphertext; // 明文直接返回
        if (_masterKey is null) return "[已加密 - 需解锁主密码]";

        try
        {
            var raw = Convert.FromBase64String(ciphertext.Substring(6));
            if (raw.Length < 28) return "[密文损坏]";

            var nonce = new byte[12];
            var tag = new byte[16];
            var cipher = new byte[raw.Length - 28];

            Buffer.BlockCopy(raw, 0, nonce, 0, 12);
            Buffer.BlockCopy(raw, 12, tag, 0, 16);
            Buffer.BlockCopy(raw, 28, cipher, 0, cipher.Length);

            var plainBytes = new byte[cipher.Length];
            using var aes = new AesGcm(_masterKey, 16);
            aes.Decrypt(nonce, cipher, tag, plainBytes);

            return Encoding.UTF8.GetString(plainBytes);
        }
        catch
        {
            return "[解密失败 - 密码错误]";
        }
    }
}
