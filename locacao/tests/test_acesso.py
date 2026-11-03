from django.contrib.auth.models import User
from django.test import TestCase


class AcessoTests(TestCase):
    def setUp(self):
        self.visualizacao = User.objects.create_user("visualizacao", password="senha-teste-123")
        self.admin = User.objects.create_user("admin", password="senha-teste-123", is_staff=True)

    def test_anonimo_redireciona_para_login(self):
        resp = self.client.get("/")
        self.assertRedirects(resp, "/login/?next=/", fetch_redirect_response=False)

    def test_visualizacao_ve_dashboard(self):
        self.client.force_login(self.visualizacao)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_visualizacao_bloqueada_em_tarefas(self):
        self.client.force_login(self.visualizacao)
        self.assertEqual(self.client.get("/tarefas/").status_code, 403)

    def test_403_tem_pagina_propria(self):
        self.client.force_login(self.visualizacao)
        resp = self.client.get("/tarefas/")
        self.assertContains(resp, "Acesso negado", status_code=403)
        self.assertContains(resp, 'href="/"', status_code=403)

    def test_login_vazio_mostra_erro_por_campo(self):
        resp = self.client.post("/login/", {"username": "", "password": ""})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'aria-invalid="true"', count=2)
        self.assertContains(resp, 'id="id_username_error"')
        self.assertContains(resp, 'id="id_password_error"')
        self.assertContains(resp, "campo-erro", count=2)

    def test_admin_ve_tarefas(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get("/tarefas/").status_code, 200)

    def test_saude_sem_login(self):
        resp = self.client.get("/saude/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b"ok")

    def test_nav_esconde_admin_para_visualizacao(self):
        self.client.force_login(self.visualizacao)
        self.assertNotContains(self.client.get("/"), 'href="/tarefas/"')

    def test_nav_mostra_admin_para_staff(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get("/"), 'href="/tarefas/"')

    def test_login_e_logout(self):
        resp = self.client.post("/login/", {"username": "visualizacao", "password": "senha-teste-123"})
        self.assertRedirects(resp, "/inicio/", fetch_redirect_response=False)
        resp = self.client.post("/logout/")
        self.assertRedirects(resp, "/login/", fetch_redirect_response=False)
        self.assertEqual(self.client.get("/").status_code, 302)
