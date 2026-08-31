import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';
import '../../core/server_store.dart';
import 'auth_controller.dart';
import 'auth_repository.dart';
import 'auth_state.dart';
import 'server_controller.dart';
import 'server_state.dart';

/// Sign-in, in two steps: which instance, then how.
///
/// The second step is decided by the server rather than the build, because a
/// published APK is installed by people running their own instances with
/// different authentication set up.
class LoginScreen extends ConsumerWidget {
  const LoginScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ServerState server = ref.watch(serverControllerProvider);
    final ThemeData theme = Theme.of(context);

    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 420),
              child: Padding(
                padding: const EdgeInsets.symmetric(
                  horizontal: 24,
                  vertical: 32,
                ),
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: <Widget>[
                    Icon(
                      Icons.restaurant_menu,
                      size: 64,
                      color: theme.colorScheme.primary,
                    ),
                    const SizedBox(height: 20),
                    Text(
                      'Pick-a-Recipe',
                      textAlign: TextAlign.center,
                      style: theme.textTheme.headlineMedium?.copyWith(
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    const SizedBox(height: 24),
                    if (server.baseUrl == null)
                      const _ServerForm()
                    else
                      const _SignInStep(),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// Step one: where the instance lives.
class _ServerForm extends ConsumerStatefulWidget {
  const _ServerForm();

  @override
  ConsumerState<_ServerForm> createState() => _ServerFormState();
}

class _ServerFormState extends ConsumerState<_ServerForm> {
  late final TextEditingController _address = TextEditingController(
    text: ref.read(serverControllerProvider).suggestedUrl ?? '',
  );

  @override
  void dispose() {
    _address.dispose();
    super.dispose();
  }

  void _submit() {
    ref.read(serverControllerProvider.notifier).connect(_address.text);
  }

  @override
  Widget build(BuildContext context) {
    final ServerState server = ref.watch(serverControllerProvider);
    final ThemeData theme = Theme.of(context);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        Text(
          'Enter the address of your Pick-a-Recipe server.',
          textAlign: TextAlign.center,
          style: theme.textTheme.bodyMedium?.copyWith(
            color: theme.colorScheme.onSurfaceVariant,
          ),
        ),
        const SizedBox(height: 24),
        if (server.errorMessage != null) ...<Widget>[
          _ErrorBanner(message: server.errorMessage!),
          const SizedBox(height: 16),
        ],
        TextField(
          controller: _address,
          enabled: !server.isBusy,
          autofocus: true,
          keyboardType: TextInputType.url,
          autocorrect: false,
          textCapitalization: TextCapitalization.none,
          textInputAction: TextInputAction.go,
          onSubmitted: (_) => _submit(),
          decoration: const InputDecoration(
            labelText: 'Server address',
            hintText: 'recipes.example.com',
            prefixIcon: Icon(Icons.dns_outlined),
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 8),
        Text(
          'Https is assumed unless you type it. Include the port if it is not '
          'the default, for example http://192.168.1.10:5006.',
          style: theme.textTheme.bodySmall?.copyWith(
            color: theme.colorScheme.onSurfaceVariant,
          ),
        ),
        const SizedBox(height: 20),
        _BusyButton(
          isBusy: server.isBusy,
          busyLabel: 'Checking\u2026',
          label: 'Connect',
          icon: Icons.arrow_forward,
          onPressed: _submit,
        ),
      ],
    );
  }
}

/// Step two: whichever way in this server actually offers.
class _SignInStep extends ConsumerWidget {
  const _SignInStep();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ServerState server = ref.watch(serverControllerProvider);
    final AuthState auth = ref.watch(authControllerProvider);
    final String baseUrl = server.baseUrl!;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        _ServerChip(baseUrl: baseUrl, isBusy: server.isBusy || auth.isBusy),
        const SizedBox(height: 20),
        if (server.isBusy && server.status == null)
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 24),
            child: Center(child: CircularProgressIndicator()),
          )
        else
          ..._body(context, ref, server, auth),
      ],
    );
  }

  List<Widget> _body(
    BuildContext context,
    WidgetRef ref,
    ServerState server,
    AuthState auth,
  ) {
    final ServerAuthStatus? status = server.status;

    // Reachable-but-not-answering: keep the address and offer a retry, since
    // an instance being briefly down is not a reason to re-type it.
    if (status == null) {
      return <Widget>[
        _ErrorBanner(
          message: server.errorMessage ?? 'Could not reach that server.',
        ),
        const SizedBox(height: 16),
        _BusyButton(
          isBusy: server.isBusy,
          busyLabel: 'Retrying\u2026',
          label: 'Try again',
          icon: Icons.refresh,
          onPressed: () =>
              ref.read(serverControllerProvider.notifier).recheck(),
        ),
      ];
    }

    if (!status.mobileAuthEnabled) {
      return <Widget>[
        const _NoticeCard(
          icon: Icons.phonelink_lock_outlined,
          message: 'This server has app sign-in switched off. Whoever runs it '
              'needs to set JWT_SECRET_KEY and restart.',
        ),
      ];
    }

    if (status.setupRequired) {
      return <Widget>[
        const _NoticeCard(
          icon: Icons.person_add_alt,
          message: 'This server has no account yet. Open it in a browser to '
              'create the first one, then come back.',
        ),
        const SizedBox(height: 16),
        OutlinedButton.icon(
          onPressed: () => ref.read(urlLauncherProvider)(
            Uri.parse('${server.baseUrl}/setup'),
          ),
          icon: const Icon(Icons.open_in_new),
          label: const Text('Open setup in browser'),
        ),
        const SizedBox(height: 12),
        _BusyButton(
          isBusy: server.isBusy,
          busyLabel: 'Checking\u2026',
          label: 'I have created it',
          icon: Icons.refresh,
          onPressed: () =>
              ref.read(serverControllerProvider.notifier).recheck(),
        ),
      ];
    }

    if (status.localAuthEnabled) {
      return <Widget>[_PasswordForm(baseUrl: server.baseUrl!)];
    }

    if (status.ssoEnabled) {
      return <Widget>[
        _Blurb(
          text: 'Sign in with your Authentik account to reach your recipes.',
        ),
        const SizedBox(height: 24),
        if (auth.errorMessage != null) ...<Widget>[
          _ErrorBanner(message: auth.errorMessage!),
          const SizedBox(height: 16),
        ],
        _BusyButton(
          isBusy: auth.isBusy,
          busyLabel: 'Waiting for browser\u2026',
          label: 'Sign in with Authentik',
          icon: Icons.login,
          onPressed: () =>
              ref.read(authControllerProvider.notifier).signIn(),
        ),
        if (auth.isBusy) ...<Widget>[
          const SizedBox(height: 12),
          _Blurb(
            text: 'Finish signing in, in the browser that just opened.',
            small: true,
          ),
        ],
      ];
    }

    // AUTH_MODE=authentik with no client credentials configured: the server
    // fails closed, and there is nothing the app can offer.
    return <Widget>[
      const _NoticeCard(
        icon: Icons.report_gmailerrorred_outlined,
        message: 'This server has no way to sign in configured. Whoever runs '
            'it needs to finish setting up Authentik, or switch to local '
            'accounts.',
      ),
    ];
  }
}

class _PasswordForm extends ConsumerStatefulWidget {
  const _PasswordForm({required this.baseUrl});

  final String baseUrl;

  @override
  ConsumerState<_PasswordForm> createState() => _PasswordFormState();
}

class _PasswordFormState extends ConsumerState<_PasswordForm> {
  final TextEditingController _username = TextEditingController();
  final TextEditingController _password = TextEditingController();
  bool _obscured = true;

  @override
  void dispose() {
    _username.dispose();
    _password.dispose();
    super.dispose();
  }

  void _submit() {
    ref.read(authControllerProvider.notifier).signInWithPassword(
          username: _username.text.trim(),
          password: _password.text,
        );
  }

  @override
  Widget build(BuildContext context) {
    final AuthState auth = ref.watch(authControllerProvider);
    final bool filled =
        _username.text.trim().isNotEmpty && _password.text.isNotEmpty;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        if (isInsecureServerUrl(widget.baseUrl)) ...<Widget>[
          const _NoticeCard(
            icon: Icons.lock_open_outlined,
            message: 'This server uses plain http, so your password travels '
                'unencrypted. Fine on a network you trust; avoid it over the '
                'internet.',
          ),
          const SizedBox(height: 16),
        ],
        if (auth.errorMessage != null) ...<Widget>[
          _ErrorBanner(message: auth.errorMessage!),
          const SizedBox(height: 16),
        ],
        TextField(
          controller: _username,
          enabled: !auth.isBusy,
          autofocus: true,
          autocorrect: false,
          textCapitalization: TextCapitalization.none,
          textInputAction: TextInputAction.next,
          onChanged: (_) => setState(() {}),
          decoration: const InputDecoration(
            labelText: 'Username',
            prefixIcon: Icon(Icons.person_outline),
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _password,
          enabled: !auth.isBusy,
          obscureText: _obscured,
          textInputAction: TextInputAction.go,
          onChanged: (_) => setState(() {}),
          onSubmitted: (_) => filled ? _submit() : null,
          decoration: InputDecoration(
            labelText: 'Password',
            prefixIcon: const Icon(Icons.lock_outline),
            border: const OutlineInputBorder(),
            suffixIcon: IconButton(
              onPressed: () => setState(() => _obscured = !_obscured),
              icon: Icon(
                _obscured ? Icons.visibility_off : Icons.visibility,
              ),
              tooltip: _obscured ? 'Show password' : 'Hide password',
            ),
          ),
        ),
        const SizedBox(height: 20),
        _BusyButton(
          isBusy: auth.isBusy,
          busyLabel: 'Signing in\u2026',
          label: 'Sign in',
          icon: Icons.login,
          onPressed: filled ? _submit : null,
        ),
      ],
    );
  }
}

/// The current address, with the way back to the address form.
class _ServerChip extends ConsumerWidget {
  const _ServerChip({required this.baseUrl, required this.isBusy});

  final String baseUrl;
  final bool isBusy;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ThemeData theme = Theme.of(context);

    return Row(
      children: <Widget>[
        Icon(
          isInsecureServerUrl(baseUrl) ? Icons.lock_open : Icons.lock_outline,
          size: 16,
          color: theme.colorScheme.onSurfaceVariant,
        ),
        const SizedBox(width: 6),
        Expanded(
          child: Text(
            baseUrl.replaceFirst(RegExp(r'^https?://'), ''),
            overflow: TextOverflow.ellipsis,
            style: theme.textTheme.bodySmall?.copyWith(
              color: theme.colorScheme.onSurfaceVariant,
            ),
          ),
        ),
        TextButton(
          onPressed: isBusy
              ? null
              : () => ref.read(serverControllerProvider.notifier).forget(),
          child: const Text('Change'),
        ),
      ],
    );
  }
}

class _Blurb extends StatelessWidget {
  const _Blurb({required this.text, this.small = false});

  final String text;
  final bool small;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Text(
      text,
      textAlign: TextAlign.center,
      style: (small ? theme.textTheme.bodySmall : theme.textTheme.bodyMedium)
          ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
    );
  }
}

class _BusyButton extends StatelessWidget {
  const _BusyButton({
    required this.isBusy,
    required this.busyLabel,
    required this.label,
    required this.icon,
    required this.onPressed,
  });

  final bool isBusy;
  final String busyLabel;
  final String label;
  final IconData icon;
  final VoidCallback? onPressed;

  @override
  Widget build(BuildContext context) {
    return FilledButton.icon(
      onPressed: isBusy ? null : onPressed,
      icon: isBusy
          ? const SizedBox.square(
              dimension: 18,
              child: CircularProgressIndicator(strokeWidth: 2),
            )
          : Icon(icon),
      label: Text(isBusy ? busyLabel : label),
      style: FilledButton.styleFrom(
        padding: const EdgeInsets.symmetric(vertical: 16),
      ),
    );
  }
}

/// Something the user cannot act on from here, phrased so they know who can.
class _NoticeCard extends StatelessWidget {
  const _NoticeCard({required this.icon, required this.message});

  final IconData icon;
  final String message;

  @override
  Widget build(BuildContext context) {
    final ColorScheme colors = Theme.of(context).colorScheme;

    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: colors.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Icon(icon, color: colors.onSurfaceVariant, size: 20),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              message,
              style: TextStyle(color: colors.onSurfaceVariant),
            ),
          ),
        ],
      ),
    );
  }
}

class _ErrorBanner extends StatelessWidget {
  const _ErrorBanner({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    final ColorScheme colors = Theme.of(context).colorScheme;

    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: colors.errorContainer,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Icon(Icons.error_outline, color: colors.onErrorContainer, size: 20),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              message,
              style: TextStyle(color: colors.onErrorContainer),
            ),
          ),
        ],
      ),
    );
  }
}
