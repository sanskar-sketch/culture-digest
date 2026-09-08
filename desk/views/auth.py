from django.contrib.auth import views as auth_views
from django.urls import reverse_lazy


class DeskLoginView(auth_views.LoginView):
    template_name = "desk/login.html"
    redirect_authenticated_user = True

    def get_success_url(self):
        return self.get_redirect_url() or reverse_lazy("desk:dashboard")


class DeskLogoutView(auth_views.LogoutView):
    next_page = reverse_lazy("desk:login")


class DeskPasswordChangeView(auth_views.PasswordChangeView):
    template_name = "desk/password_change.html"
    success_url = reverse_lazy("desk:password_change_done")


class DeskPasswordChangeDoneView(auth_views.PasswordChangeDoneView):
    template_name = "desk/password_change_done.html"
