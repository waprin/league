// Generated by CoffeeScript 1.8.0
(function() {
  var __hasProp = {}.hasOwnProperty,
    __extends = function(child, parent) { for (var key in parent) { if (__hasProp.call(parent, key)) child[key] = parent[key]; } function ctor() { this.constructor = child; } ctor.prototype = parent.prototype; child.prototype = new ctor(); child.__super__ = parent.prototype; return child; };

  define(['jquery', 'underscore', 'backbone', 'lib/text!app/templates/league_template.ejs'], function($, _, Backbone, league_template) {
    var AppView, EspnLeagues, League, LeagueView;
    League = (function(_super) {
      __extends(League, _super);

      function League() {
        return League.__super__.constructor.apply(this, arguments);
      }

      return League;

    })(Backbone.Model);
    EspnLeagues = (function(_super) {
      __extends(EspnLeagues, _super);

      function EspnLeagues() {
        return EspnLeagues.__super__.constructor.apply(this, arguments);
      }

      EspnLeagues.prototype.url = '/leagues';

      EspnLeagues.prototype.model = League;

      EspnLeagues.prototype.initialize = function() {
        return _.bindAll(this, 'fetch');
      };

      return EspnLeagues;

    })(Backbone.Collection);
    LeagueView = (function(_super) {
      __extends(LeagueView, _super);

      function LeagueView() {
        return LeagueView.__super__.constructor.apply(this, arguments);
      }

      LeagueView.prototype.tagName = 'li';

      LeagueView.prototype.template = _.template(league_template);

      LeagueView.prototype.initialize = function() {
        return this.listenTo(this.model, 'change', this.render);
      };

      LeagueView.prototype.render = function() {
        var model;
        model = this.model.toJSON();
        model.percent_done = (100 * model.pages_scraped) / model.total_pages;
        this.$el.html(this.template(model));
        return this;
      };

      return LeagueView;

    })(Backbone.View);
    AppView = (function(_super) {
      __extends(AppView, _super);

      function AppView() {
        return AppView.__super__.constructor.apply(this, arguments);
      }

      AppView.prototype.el = $("#accounts_app");

      AppView.prototype.initialize = function() {
        this.listenTo(this.collection, 'add', this.addOne);
        this.collection.fetch();
        return this.collection.on('sync', (function(_this) {
          return function() {
            var isLoaded;
            isLoaded = function(model) {
              return model.get('loaded');
            };
            if (!_.every(_this.collection.models, isLoaded)) {
              return setTimeout(_this.collection.fetch, 5000);
            }
          };
        })(this));
      };

      AppView.prototype.addOne = function(league) {
        var leagueView;
        leagueView = new LeagueView({
          model: league
        });
        return $('#accounts_list').append(leagueView.render().el);
      };

      return AppView;

    })(Backbone.View);
    return {
      League: League,
      EspnLeagues: EspnLeagues,
      LeagueView: LeagueView,
      AppView: AppView
    };
  });

}).call(this);